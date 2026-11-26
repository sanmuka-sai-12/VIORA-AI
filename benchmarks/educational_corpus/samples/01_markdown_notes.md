# Classical Kinematics

Kinematics describes motion without reference to forces.
Average velocity equals displacement divided by elapsed time.

## Equations of Constant Acceleration
The final velocity satisfies $v = u + at$.
$$\Delta x = v_0 t + \frac{1}{2} a t^2$$

## Simulation Snippet
```python
def position(x0: float, v0: float, a: float, t: float) -> float:
    return x0 + v0 * t + 0.5 * a * t * t
```

- SI unit of velocity: m/s
- SI unit of acceleration: m/s^2
