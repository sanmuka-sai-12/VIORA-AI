#include <assert.h>
#include <stdio.h>

#define V_DAG 5
#define Q_FIXED 5
#define Q_LARGE 32

typedef struct {
    int pushes;
    int expansions;
    int capacity_violated;
    int step_limit_hit;
} BfsMetrics;

static int checked_push(int *q, int cap, int *tail, int val, BfsMetrics *m) {
    m->pushes++;
    if (*tail >= cap) {
        m->capacity_violated = 1;
        return 0; /* Refuse OOB write instead of invoking C undefined behavior */
    }
    q[(*tail)++] = val;
    return 1;
}

static BfsMetrics bfs_enqueue_mark(int n, const int adj[V_DAG][V_DAG], int q_cap, int dist[V_DAG]) {
    int q[Q_LARGE];
    int head = 0, tail = 0;
    BfsMetrics m = {0};
    for (int i = 0; i < n; ++i) dist[i] = -1;

    dist[0] = 0;
    checked_push(q, q_cap, &tail, 0, &m);
    while (head < tail) {
        int u = q[head++];
        m.expansions++;
        for (int v = 0; v < n; ++v) {
            if (adj[u][v] && dist[v] == -1) {
                dist[v] = dist[u] + 1;
                checked_push(q, q_cap, &tail, v, &m);
            }
        }
    }
    return m;
}

static BfsMetrics bfs_dequeue_mark_guarded(int n, const int adj[V_DAG][V_DAG], int q_cap, int dist[V_DAG]) {
    int q[Q_LARGE];
    int visited[V_DAG] = {0};
    int head = 0, tail = 0;
    BfsMetrics m = {0};
    for (int i = 0; i < n; ++i) dist[i] = -1;

    dist[0] = 0;
    checked_push(q, q_cap, &tail, 0, &m);
    while (head < tail) {
        int u = q[head++];
        if (visited[u]) continue; /* Duplicate-pop guard bounds vertex expansions to <= V */
        visited[u] = 1;
        m.expansions++;
        for (int v = 0; v < n; ++v) {
            if (adj[u][v] && !visited[v]) {
                if (dist[v] == -1) dist[v] = dist[u] + 1;
                checked_push(q, q_cap, &tail, v, &m);
            }
        }
    }
    return m;
}

static BfsMetrics bfs_dequeue_mark_unguarded_cycle(int n, const int adj[3][3], int q_cap, int budget) {
    int q[Q_LARGE];
    int head = 0, tail = 0;
    BfsMetrics m = {0};

    checked_push(q, q_cap, &tail, 0, &m);
    while (head < tail) {
        if (m.expansions >= budget) {
            m.step_limit_hit = 1;
            break;
        }
        int u = q[head++];
        m.expansions++;
        /* Unguarded: never checks visited before neighbor expansion, so cyclic edges re-enqueue */
        for (int v = 0; v < n; ++v) {
            if (adj[u][v]) checked_push(q, q_cap, &tail, v, &m);
        }
    }
    return m;
}

int main(void) {
    /* Acyclic bipartite diamond K_{1,2,2} (V=5, E=6): nodes 1 & 2 share children 3 & 4 */
    const int dag[V_DAG][V_DAG] = {
        {0, 1, 1, 0, 0},
        {0, 0, 0, 1, 1},
        {0, 0, 0, 1, 1},
        {0, 0, 0, 0, 0},
        {0, 0, 0, 0, 0},
    };
    int dist[V_DAG];

    /* 1. Enqueue-time marking fits int q[V] with exact V=5 pushes */
    BfsMetrics m_enq = bfs_enqueue_mark(V_DAG, dag, Q_FIXED, dist);
    assert(m_enq.pushes == 5 && m_enq.expansions == 5 && m_enq.capacity_violated == 0);
    assert(dist[0] == 0 && dist[1] == 1 && dist[2] == 1 && dist[3] == 2 && dist[4] == 2);

    /* 2a. Pop-time marking WITH guard on fixed q[5]: duplicate pushes (7 > 5) hit capacity check safely */
    BfsMetrics m_guard_fixed = bfs_dequeue_mark_guarded(V_DAG, dag, Q_FIXED, dist);
    assert(m_guard_fixed.pushes == 7 && m_guard_fixed.capacity_violated == 1);

    /* 2b. Pop-time marking WITH guard on larger queue q[32]: expands each vertex once cleanly */
    BfsMetrics m_guard_large = bfs_dequeue_mark_guarded(V_DAG, dag, Q_LARGE, dist);
    assert(m_guard_large.pushes == 7 && m_guard_large.expansions == 5 && m_guard_large.capacity_violated == 0);
    assert(dist[3] == 2 && dist[4] == 2);

    /* 3. Pop-time marking WITHOUT guard on 3-vertex cycle: exceeds V, hits budget/capacity safely */
    const int cyc3[3][3] = {
        {0, 1, 0},
        {0, 0, 1},
        {1, 0, 0},
    };
    BfsMetrics m_unguard = bfs_dequeue_mark_unguarded_cycle(3, cyc3, Q_LARGE, 10);
    assert(m_unguard.step_limit_hit == 1 && m_unguard.pushes > 3 && m_unguard.expansions == 10);

    printf("PASS bfs_duplicate_counterexample (enq=%d, guard=%d/%d, unguard_exp=%d)\n",
           m_enq.pushes, m_guard_large.pushes, m_guard_large.expansions, m_unguard.expansions);
    return 0;
}
