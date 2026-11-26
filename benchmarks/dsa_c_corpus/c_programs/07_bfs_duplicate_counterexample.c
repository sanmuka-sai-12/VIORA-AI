#include <assert.h>
#include <stdio.h>

#define V 5
#define Q_LARGE 32

typedef enum { MARK_ENQUEUE, MARK_DEQUEUE_GUARDED, MARK_DEQUEUE_UNGUARDED } MarkMode;
typedef enum { LINEAR, CIRCULAR } QueueMode;

typedef struct {
    int pushes; /* Successful insertions, not attempted writes. */
    int expansions;
    int max_occupancy;
    int capacity_violated;
} BfsMetrics;

/* Matrix traversal scans V columns per expansion; adjacency lists scan only
 * outgoing edges. A circular queue reuses popped slots, unlike a linear tail. */
static BfsMetrics bfs(const int adj[V][V], MarkMode mark, QueueMode queue,
                      int capacity, int dist[V]) {
    int q[Q_LARGE];
    int visited[V] = {0};
    int head = 0, tail = 0, count = 1;
    BfsMetrics m = {1, 0, 1, 0};
    assert(capacity > 0 && capacity <= Q_LARGE);
    for (int v = 0; v < V; ++v) dist[v] = -1;
    dist[0] = 0;
    visited[0] = mark == MARK_ENQUEUE;
    q[tail++] = 0;
    if (queue == CIRCULAR) tail %= capacity;
    while (count > 0) {
        int u = q[head++];
        if (queue == CIRCULAR) head %= capacity;
        --count;
        if (mark == MARK_DEQUEUE_GUARDED && visited[u]) continue;
        visited[u] = 1;
        ++m.expansions;
        for (int v = 0; v < V; ++v) {
            if (!adj[u][v] || visited[v]) continue;
            if ((queue == LINEAR && tail >= capacity) || count >= capacity) {
                m.capacity_violated = 1;
                return m; /* Stop explicitly before any out-of-bounds write. */
            }
            if (dist[v] == -1) dist[v] = dist[u] + 1;
            if (mark == MARK_ENQUEUE) visited[v] = 1;
            q[tail++] = v;
            if (queue == CIRCULAR) tail %= capacity;
            ++count;
            ++m.pushes;
            if (count > m.max_occupancy) m.max_occupancy = count;
        }
    }
    return m;
}

int main(void) {
    /* Five vertices, six edges: two parents share two children. */
    const int diamond[V][V] = {
        {0, 1, 1, 0, 0},
        {0, 0, 0, 1, 1},
        {0, 0, 0, 1, 1},
        {0, 0, 0, 0, 0},
        {0, 0, 0, 0, 0},
    };
    /* Add back edges: real dequeue marking still terminates on this cycle. */
    const int cyclic_diamond[V][V] = {
        {0, 1, 1, 0, 0},
        {0, 0, 0, 1, 1},
        {0, 0, 0, 1, 1},
        {1, 0, 0, 0, 0},
        {1, 0, 0, 0, 0},
    };
    int dist[V];
    BfsMetrics enq = bfs(diamond, MARK_ENQUEUE, LINEAR, V, dist);
    assert(enq.pushes == V && enq.expansions == V && !enq.capacity_violated);
    assert(enq.max_occupancy == 3);
    assert(dist[0] == 0 && dist[1] == 1 && dist[2] == 1 && dist[3] == 2 && dist[4] == 2);

    BfsMetrics fixed = bfs(diamond, MARK_DEQUEUE_GUARDED, LINEAR, V, dist);
    assert(fixed.pushes == V && fixed.capacity_violated);
    BfsMetrics guarded = bfs(diamond, MARK_DEQUEUE_GUARDED, LINEAR, Q_LARGE, dist);
    assert(guarded.pushes == 7 && guarded.expansions == V && !guarded.capacity_violated);
    assert(guarded.max_occupancy == 4);
    assert(dist[3] == 2 && dist[4] == 2);
    BfsMetrics ring = bfs(diamond, MARK_DEQUEUE_GUARDED, CIRCULAR, V, dist);
    assert(ring.pushes == 7 && ring.max_occupancy == 4 && !ring.capacity_violated);
    BfsMetrics small_ring = bfs(diamond, MARK_DEQUEUE_GUARDED, CIRCULAR, 3, dist);
    assert(small_ring.capacity_violated);

    BfsMetrics unguarded = bfs(cyclic_diamond, MARK_DEQUEUE_UNGUARDED, LINEAR, Q_LARGE, dist);
    assert(unguarded.pushes == 7 && unguarded.expansions == 7);
    assert(unguarded.max_occupancy == 4 && !unguarded.capacity_violated);
    BfsMetrics cyclic_guarded = bfs(cyclic_diamond, MARK_DEQUEUE_GUARDED, CIRCULAR, V, dist);
    assert(cyclic_guarded.pushes == 7 && cyclic_guarded.expansions == V);
    assert(!cyclic_guarded.capacity_violated);
    BfsMetrics cyclic_enq = bfs(cyclic_diamond, MARK_ENQUEUE, LINEAR, V, dist);
    assert(cyclic_enq.pushes == V && cyclic_enq.expansions == V && !cyclic_enq.capacity_violated);

    printf("PASS bfs_duplicate_counterexample: enqueue=%d/%d, guarded=%d/%d, "
           "unguarded_cycle=%d/%d, peak=%d, linear_full=%d, ring_full=%d\n",
           enq.pushes, enq.expansions, guarded.pushes, guarded.expansions,
           unguarded.pushes, unguarded.expansions, guarded.max_occupancy,
           fixed.capacity_violated, ring.capacity_violated);
    return 0;
}
