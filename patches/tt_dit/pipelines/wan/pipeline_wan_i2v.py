# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC / © 2025 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Hotpatch: pipeline_wan_i2v.py
# Applied via patches/tt_dit/ bind-mount (start_animate.sh --dev-mode).
#
# Fix: get_model_input receives cond_latents as a torch.Tensor (the output of
# prepare_latents' tt_y), but unflatten() calls ttnn.reshape() which requires a
# TTNN tensor.  Convert cond_latents to TTNN bf16 before the concat.
#
# All other code is identical to the container's pipeline_wan_i2v.py.

import os
from typing import List, NamedTuple, Optional, Union

import torch
from diffusers.schedulers import UniPCMultistepScheduler
from PIL import Image

import ttnn

from ...models.vae.vae_wan2_1 import WanEncoder
from ...utils import cache
from ...utils.conv3d import conv_pad_height, conv_pad_in_channels
from ...utils.tensor import bf16_tensor_2dshard, fast_device_to_host, unflatten
from .pipeline_wan import WanPipeline


class ImagePrompt(NamedTuple):
    image: Image.Image
    frame_pos: int


class WanPipelineI2V(WanPipeline):
    def __init__(self, *args, height: int = 0, width: int = 0, **kwargs):
        if "checkpoint_name" not in kwargs:
            kwargs["checkpoint_name"] = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
        if "scheduler" not in kwargs:
            kwargs["scheduler"] = UniPCMultistepScheduler.from_pretrained(
                kwargs["checkpoint_name"], subfolder="scheduler", trust_remote_code=True
            )

        super().__init__(*args, model_type="i2v", run_warmup=False, height=height, width=width, **kwargs)

        self.tt_vae_encoder = WanEncoder(
            base_dim=self.vae.config.base_dim,
            in_channels=self.vae.config.in_channels,
            z_dim=self.vae.config.z_dim,
            dim_mult=self.vae.config.dim_mult,
            num_res_blocks=self.vae.config.num_res_blocks,
            attn_scales=self.vae.config.attn_scales,
            temperal_downsample=self.vae.config.temperal_downsample,
            is_residual=self.vae.config.is_residual,
            mesh_device=self.mesh_device,
            ccl_manager=self.vae_ccl_manager,
            parallel_config=self.vae_parallel_config,
        )

        cache.load_model(
            self.tt_vae_encoder,
            model_name=os.path.basename(self.checkpoint_name),
            subfolder="vae_encoder",
            parallel_config=self.vae_parallel_config,
            mesh_shape=tuple(self.mesh_device.shape),
            get_torch_state_dict=lambda: self.vae.state_dict(),
        )

        self.warmup_buffers(height=height, width=width, image_prompt=Image.new("RGB", (width, height)))

    @staticmethod
    def create_pipeline(*args, **kwargs):
        if "checkpoint_name" not in kwargs:
            kwargs["checkpoint_name"] = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
        return WanPipeline.create_pipeline(*args, pipeline_class=WanPipelineI2V, **kwargs)

    def get_model_input(self, latents, cond_latents):
        """
        Adapter function to enable I2V. Concatenates noisy latents with the
        conditioning latents (image + mask) along the last dimension.

        Fix (hotpatch): cond_latents arrives as a torch.Tensor from prepare_latents.
        unflatten() → ttnn.reshape() requires a TTNN tensor, so we convert it here
        using the same mesh/dtype as the already-TTNN latents tensor.
        """
        latents = super().get_model_input(latents, None)
        z_dim = self.vae.config.z_dim
        t_size = latents.shape[-1]

        # Convert cond_latents from torch → TTNN if needed (fix for reshape error)
        if isinstance(cond_latents, torch.Tensor):
            cond_latents = ttnn.from_torch(
                cond_latents.to(torch.bfloat16).contiguous(),
                dtype=ttnn.bfloat16,
                layout=ttnn.TILE_LAYOUT,
                device=self.mesh_device,
                mesh_mapper=ttnn.ReplicateTensorToMesh(self.mesh_device),
            )

        model_input = ttnn.concat(
            [unflatten(latents, -1, (t_size // z_dim, -1)), unflatten(cond_latents, -1, (t_size // z_dim, -1))],
            dim=-1,
        )
        return ttnn.reshape(model_input, (*tuple(latents.shape)[:-1], -1))

    def prepare_latents(
        self,
        batch_size: int,
        image_prompt: Union[ImagePrompt, Image.Image, List[ImagePrompt]],
        num_channels_latents: int = 16,
        height: int = 480,
        width: int = 832,
        num_frames: int = 81,
        dtype: Optional[torch.dtype] = None,
        device: Optional[torch.device] = None,
        latents: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        assert batch_size == 1, "Only batch size 1 is currently supported for I2V"

        if isinstance(image_prompt, ImagePrompt):
            image_prompt = [image_prompt]
        elif isinstance(image_prompt, Image.Image):
            image_prompt = [ImagePrompt(image=image_prompt, frame_pos=0)]

        latents, _ = super().prepare_latents(
            batch_size=batch_size,
            num_channels_latents=num_channels_latents,
            height=height,
            width=width,
            num_frames=num_frames,
            dtype=dtype,
            device=device,
            latents=latents,
        )

        latent_shape = latents.shape

        msk = torch.zeros(batch_size, num_frames, latent_shape[-2], latent_shape[-1])
        inserted_frames = set()
        video_condition = None
        for image, frame_pos in image_prompt:
            assert (
                frame_pos not in inserted_frames
            ), f"Frame position {frame_pos} already processed."
            inserted_frames.add(frame_pos)
            image = self.video_processor.preprocess(image, height=height, width=width).to(device, dtype=torch.float32)

            if video_condition is None:
                video_condition = image.new_zeros(image.shape[0], image.shape[1], num_frames, height, width)
            video_condition[:, :, frame_pos, :, :] = image
            msk[:, frame_pos, :, :] = 1

        tt_video_condition_BTHWC = video_condition.permute(0, 2, 3, 4, 1)
        tt_video_condition_BTHWC = conv_pad_in_channels(tt_video_condition_BTHWC)
        tt_video_condition_BTHWC, logical_h = conv_pad_height(
            tt_video_condition_BTHWC, self.vae_parallel_config.height_parallel.factor * self.vae_scale_factor_spatial
        )
        tt_video_condition_BTHWC = bf16_tensor_2dshard(
            tt_video_condition_BTHWC,
            self.mesh_device,
            layout=ttnn.ROW_MAJOR_LAYOUT,
            shard_mapping={
                self.vae_parallel_config.height_parallel.mesh_axis: 2,
                self.vae_parallel_config.width_parallel.mesh_axis: 3,
            },
        )

        encoded_video_BCTHW, new_logical_h = self.tt_vae_encoder(tt_video_condition_BTHWC, logical_h)

        concat_dims = [None, None]
        concat_dims[self.vae_parallel_config.height_parallel.mesh_axis] = 3
        concat_dims[self.vae_parallel_config.width_parallel.mesh_axis] = 4
        encoded_video_torch = fast_device_to_host(
            encoded_video_BCTHW,
            self.mesh_device,
            concat_dims,
            ccl_manager=self.vae_ccl_manager,
        )
        encoded_video_torch = encoded_video_torch[:, :, :, :new_logical_h, :]
        encoded_video_torch = encoded_video_torch.to(dtype=dtype)

        latents_mean = (
            torch.tensor(self.vae.config.latents_mean)
            .view(1, self.vae.config.z_dim, 1, 1, 1)
            .to(encoded_video_torch.device, encoded_video_torch.dtype)
        )
        latents_std = 1.0 / torch.tensor(self.vae.config.latents_std).view(1, self.vae.config.z_dim, 1, 1, 1).to(
            encoded_video_torch.device, encoded_video_torch.dtype
        )

        encoded_video_torch = (encoded_video_torch - latents_mean) * latents_std

        msk = torch.concat([torch.repeat_interleave(msk[:, 0:1], repeats=4, dim=1), msk[:, 1:]], dim=1)
        msk = msk.view(1, msk.shape[1] // 4, 4, latent_shape[-2], latent_shape[-1])
        msk = msk.transpose(1, 2)

        tt_y = torch.cat([msk, encoded_video_torch], dim=1)

        return latents, tt_y
