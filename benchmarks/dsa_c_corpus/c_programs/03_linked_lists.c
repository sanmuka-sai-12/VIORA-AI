#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct SNode {
    int val;
    struct SNode *next;
} SNode;

static int sll_push_front(SNode **head, int val) {
    SNode *n = (SNode *)malloc(sizeof(SNode));
    if (!n) return -1;
    n->val = val;
    n->next = *head;
    *head = n;
    return 0;
}

static void sll_reverse(SNode **head) {
    SNode *prev = NULL, *cur = *head;
    while (cur) {
        SNode *nxt = cur->next;
        cur->next = prev;
        prev = cur;
        cur = nxt;
    }
    *head = prev;
}

typedef struct DNode {
    int val;
    struct DNode *prev;
    struct DNode *next;
} DNode;

static void dcl_insert_after(DNode *pos, DNode *node) {
    node->next = pos->next;
    node->prev = pos;
    pos->next->prev = node;
    pos->next = node;
}

static void dcl_unlink(DNode *node) {
    node->prev->next = node->next;
    node->next->prev = node->prev;
}

int main(void) {
    SNode *head = NULL;
    assert(sll_push_front(&head, 3) == 0);
    assert(sll_push_front(&head, 2) == 0);
    assert(sll_push_front(&head, 1) == 0);
    sll_reverse(&head);
    assert(head->val == 3 && head->next->val == 2 && head->next->next->val == 1);
    while (head) { SNode *t = head->next; free(head); head = t; }

    DNode sentinel = {0, &sentinel, &sentinel};
    DNode a = {10, NULL, NULL}, b = {20, NULL, NULL};
    dcl_insert_after(&sentinel, &a);
    dcl_insert_after(&a, &b);
    assert(sentinel.next == &a && sentinel.prev == &b);
    dcl_unlink(&a);
    assert(sentinel.next == &b && sentinel.prev == &b);
    printf("PASS linked_lists\n");
    return 0;
}
