#include <assert.h>
#include <stddef.h>
#include <stdio.h>

#define CAP 4

typedef struct {
    int items[CAP];
    int top;
} ArrayStack;

static void stack_init(ArrayStack *s) { s->top = -1; }
static int stack_push(ArrayStack *s, int v) {
    if (s->top >= CAP - 1) return -1;
    s->items[++s->top] = v;
    return 0;
}
static int stack_pop(ArrayStack *s, int *out) {
    if (s->top < 0) return -1;
    *out = s->items[s->top--];
    return 0;
}

typedef struct {
    int items[CAP];
    size_t head;
    size_t count;
} RingQueue;

static void queue_init(RingQueue *q) { q->head = 0; q->count = 0; }
static int queue_enqueue(RingQueue *q, int v) {
    if (q->count == CAP) return -1;
    size_t tail = (q->head + q->count) % CAP;
    q->items[tail] = v;
    q->count++;
    return 0;
}
static int queue_dequeue(RingQueue *q, int *out) {
    if (q->count == 0) return -1;
    *out = q->items[q->head];
    q->head = (q->head + 1) % CAP;
    q->count--;
    return 0;
}

int main(void) {
    ArrayStack st;
    int x = 0;
    stack_init(&st);
    assert(stack_pop(&st, &x) == -1);
    assert(stack_push(&st, 10) == 0 && stack_push(&st, 20) == 0);
    assert(stack_pop(&st, &x) == 0 && x == 20);
    assert(stack_pop(&st, &x) == 0 && x == 10);

    RingQueue q;
    queue_init(&q);
    for (int i = 1; i <= CAP; ++i) assert(queue_enqueue(&q, i) == 0);
    assert(queue_enqueue(&q, 99) == -1);
    assert(queue_dequeue(&q, &x) == 0 && x == 1);
    assert(queue_dequeue(&q, &x) == 0 && x == 2);
    assert(queue_enqueue(&q, 5) == 0 && queue_enqueue(&q, 6) == 0);
    assert(queue_dequeue(&q, &x) == 0 && x == 3);
    assert(queue_dequeue(&q, &x) == 0 && x == 4);
    assert(queue_dequeue(&q, &x) == 0 && x == 5);
    assert(queue_dequeue(&q, &x) == 0 && x == 6);
    assert(queue_dequeue(&q, &x) == -1);
    printf("PASS stacks_queues\n");
    return 0;
}
