#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct BstNode {
    int key;
    struct BstNode *left, *right;
} BstNode;

static BstNode *bst_insert(BstNode *root, int key) {
    if (!root) {
        BstNode *n = (BstNode *)calloc(1, sizeof(BstNode));
        assert(n != NULL);
        n->key = key;
        return n;
    }
    if (key < root->key) root->left = bst_insert(root->left, key);
    else if (key > root->key) root->right = bst_insert(root->right, key);
    return root;
}

static void bst_inorder(const BstNode *root, int *out, size_t *idx) {
    if (!root) return;
    bst_inorder(root->left, out, idx);
    out[(*idx)++] = root->key;
    bst_inorder(root->right, out, idx);
}

static void bst_free(BstNode *root) {
    if (!root) return;
    bst_free(root->left);
    bst_free(root->right);
    free(root);
}

typedef struct {
    int items[16];
    size_t size;
} MinHeap;

static void heap_push(MinHeap *h, int val) {
    size_t i = h->size++;
    h->items[i] = val;
    while (i > 0) {
        size_t p = (i - 1) / 2;
        if (h->items[p] <= h->items[i]) break;
        int tmp = h->items[p]; h->items[p] = h->items[i]; h->items[i] = tmp;
        i = p;
    }
}

static int heap_pop(MinHeap *h) {
    int root = h->items[0];
    h->items[0] = h->items[--h->size];
    size_t i = 0;
    while (2 * i + 1 < h->size) {
        size_t c = 2 * i + 1;
        if (c + 1 < h->size && h->items[c + 1] < h->items[c]) c++;
        if (h->items[i] <= h->items[c]) break;
        int tmp = h->items[i]; h->items[i] = h->items[c]; h->items[c] = tmp;
        i = c;
    }
    return root;
}

int main(void) {
    int keys[] = {8, 3, 10, 1, 6, 14};
    BstNode *root = NULL;
    for (size_t i = 0; i < 6; ++i) root = bst_insert(root, keys[i]);
    int sorted[6]; size_t idx = 0;
    bst_inorder(root, sorted, &idx);
    int exp[] = {1, 3, 6, 8, 10, 14};
    for (size_t i = 0; i < 6; ++i) assert(sorted[i] == exp[i]);
    bst_free(root);

    MinHeap mh = {{0}, 0};
    for (size_t i = 0; i < 6; ++i) heap_push(&mh, keys[i]);
    for (size_t i = 0; i < 6; ++i) assert(heap_pop(&mh) == exp[i]);
    printf("PASS trees_heaps\n");
    return 0;
}
