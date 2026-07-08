import turtle

def draw_tree(t, branch_length, angle, shrink_factor, min_length):
    if branch_length > min_length:
        t.forward(branch_length)
        t.left(angle)
        draw_tree(t, branch_length * shrink_factor, angle, shrink_factor, min_length)
        t.right(2 * angle)
        draw_tree(t, branch_length * shrink_factor, angle, shrink_factor, min_length)
        t.left(angle)
        t.backward(branch_length)

def main():
    window = turtle.Screen()
    window.setup(800, 600)
    t = turtle.Turtle()
    t.speed(0)
    t.left(90)
    draw_tree(t, 100, 30, 0.7, 5)
    window.mainloop()

if __name__ == "__main__":
    main()