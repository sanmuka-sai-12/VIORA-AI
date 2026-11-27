#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

#define HT_CAP 11
typedef enum { HT_EMPTY = 0, HT_OCCUPIED, HT_TOMBSTONE } SlotState;

typedef struct {
    int key;
    int val;
    SlotState state;
} HashSlot;

static size_t hash_int(int k) {
    unsigned int u = (unsigned int)k;
    return (size_t)(u % HT_CAP);
}

static int ht_put(HashSlot *tbl, int key, int val) {
    size_t idx = hash_int(key), first_tomb = HT_CAP;
    for (size_t step = 0; step < HT_CAP; ++step) {
        size_t pos = (idx + step) % HT_CAP;
        if (tbl[pos].state == HT_EMPTY) {
            size_t dst = (first_tomb < HT_CAP) ? first_tomb : pos;
            tbl[dst] = (HashSlot){key, val, HT_OCCUPIED};
            return 0;
        }
        if (tbl[pos].state == HT_TOMBSTONE && first_tomb == HT_CAP) first_tomb = pos;
        else if (tbl[pos].state == HT_OCCUPIED && tbl[pos].key == key) {
            tbl[pos].val = val;
            return 0;
        }
    }
    if (first_tomb < HT_CAP) {
        tbl[first_tomb] = (HashSlot){key, val, HT_OCCUPIED};
        return 0;
    }
    return -1;
}

static int ht_get(const HashSlot *tbl, int key, int *out) {
    size_t idx = hash_int(key);
    for (size_t step = 0; step < HT_CAP; ++step) {
        size_t pos = (idx + step) % HT_CAP;
        if (tbl[pos].state == HT_EMPTY) return -1;
        if (tbl[pos].state == HT_OCCUPIED && tbl[pos].key == key) { *out = tbl[pos].val; return 0; }
    }
    return -1;
}

static int ht_delete(HashSlot *tbl, int key) {
    size_t idx = hash_int(key);
    for (size_t step = 0; step < HT_CAP; ++step) {
        size_t pos = (idx + step) % HT_CAP;
        if (tbl[pos].state == HT_EMPTY) return -1;
        if (tbl[pos].state == HT_OCCUPIED && tbl[pos].key == key) {
            tbl[pos].state = HT_TOMBSTONE;
            return 0;
        }
    }
    return -1;
}

static void bfs_distances(int n, const int adj[5][5], int start, int dist[5]) {
    int q[5], head = 0, tail = 0;
    for (int i = 0; i < n; ++i) dist[i] = -1;
    dist[start] = 0;
    q[tail++] = start;
    while (head < tail) {
        int u = q[head++];
        for (int v = 0; v < n; ++v) {
            if (adj[u][v] && dist[v] == -1) {
                dist[v] = dist[u] + 1;
                q[tail++] = v;
            }
        }
    }
}

int main(void) {
    HashSlot tbl[HT_CAP] = {{0}};
    assert(ht_put(tbl, 15, 100) == 0);
    assert(ht_put(tbl, 26, 200) == 0); /* collides 15 % 11 == 26 % 11 == 4 */
    assert(ht_delete(tbl, 15) == 0);
    assert(ht_delete(tbl, 99) == -1);
    int val = 0;
    assert(ht_get(tbl, 26, &val) == 0 && val == 200);
    assert(ht_get(tbl, 15, &val) == -1);

    const int adj[5][5] = {
        {0, 1, 1, 0, 0},
        {1, 0, 0, 1, 0},
        {1, 0, 0, 1, 0},
        {0, 1, 1, 0, 1},
        {0, 0, 0, 1, 0},
    };
    int dist[5];
    bfs_distances(5, adj, 0, dist);
    assert(dist[0] == 0 && dist[1] == 1 && dist[2] == 1 && dist[3] == 2 && dist[4] == 3);
    printf("PASS graphs_hashing\n");
    return 0;
}
