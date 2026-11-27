# VIORA-DSA Study Notes: Dynamic Arrays and Circular Queues

A dynamic array doubles capacity (`new_cap = capacity * 2`) when full so amortized push stays O(1).

## Ring-Buffer FIFO Equations

Given capacity CAP, current head index, and item count:

$$tail = (head + count) \bmod CAP$$

```c
size_t tail = (q->head + q->count) % CAP;
q->items[tail] = val;
q->count++;
```
