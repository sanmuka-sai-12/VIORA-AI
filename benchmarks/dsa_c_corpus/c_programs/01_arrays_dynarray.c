#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int *data;
    size_t size;
    size_t capacity;
} DynArray;

static int dyn_init(DynArray *arr, size_t initial_cap) {
    arr->data = (int *)malloc(initial_cap * sizeof(int));
    if (!arr->data) return -1;
    arr->size = 0;
    arr->capacity = initial_cap;
    return 0;
}

static int dyn_push(DynArray *arr, int value) {
    if (arr->size == arr->capacity) {
        size_t new_cap = arr->capacity ? arr->capacity * 2 : 4;
        int *tmp = (int *)realloc(arr->data, new_cap * sizeof(int));
        if (!tmp) return -1;
        arr->data = tmp;
        arr->capacity = new_cap;
    }
    arr->data[arr->size++] = value;
    return 0;
}

static void swap_ints(int *a, int *b) {
    int tmp = *a;
    *a = *b;
    *b = tmp;
}

int main(void) {
    int vals[4] = {10, 20, 30, 40};
    int *p = vals;
    assert(*(p + 2) == 30);
    swap_ints(&vals[0], &vals[3]);
    assert(vals[0] == 40 && vals[3] == 10);

    DynArray da;
    assert(dyn_init(&da, 2) == 0);
    for (int i = 0; i < 9; ++i) {
        assert(dyn_push(&da, i * 3) == 0);
    }
    assert(da.size == 9);
    assert(da.capacity == 16);
    assert(da.data[8] == 24);
    free(da.data);
    printf("PASS arrays_dynarray\n");
    return 0;
}
