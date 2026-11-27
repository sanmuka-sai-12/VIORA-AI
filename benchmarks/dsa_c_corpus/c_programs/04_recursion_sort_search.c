#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

static int binary_search_int(const int *arr, size_t n, int target) {
    size_t lo = 0, hi = n;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (arr[mid] == target) return (int)mid;
        if (arr[mid] < target) lo = mid + 1;
        else hi = mid;
    }
    return -1;
}

static void merge_ranges(int *arr, int *tmp, size_t lo, size_t mid, size_t hi) {
    size_t i = lo, j = mid, k = lo;
    while (i < mid && j < hi) {
        if (arr[i] <= arr[j]) tmp[k++] = arr[i++];
        else tmp[k++] = arr[j++];
    }
    while (i < mid) tmp[k++] = arr[i++];
    while (j < hi) tmp[k++] = arr[j++];
    for (size_t idx = lo; idx < hi; ++idx) arr[idx] = tmp[idx];
}

static void merge_sort_rec(int *arr, int *tmp, size_t lo, size_t hi) {
    if (hi - lo <= 1) return;
    size_t mid = lo + (hi - lo) / 2;
    merge_sort_rec(arr, tmp, lo, mid);
    merge_sort_rec(arr, tmp, mid, hi);
    merge_ranges(arr, tmp, lo, mid, hi);
}

int main(void) {
    int a[] = {38, 27, 43, 3, 9, 82, 10};
    int tmp[7];
    merge_sort_rec(a, tmp, 0, 7);
    int expected[] = {3, 9, 10, 27, 38, 43, 82};
    for (size_t i = 0; i < 7; ++i) assert(a[i] == expected[i]);
    assert(binary_search_int(a, 7, 27) == 3);
    assert(binary_search_int(a, 7, 3) == 0);
    assert(binary_search_int(a, 7, 99) == -1);
    assert(binary_search_int(a, 0, 3) == -1);
    printf("PASS recursion_sort_search\n");
    return 0;
}
