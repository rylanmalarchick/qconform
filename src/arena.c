#include "arena.h"

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define BLOCK_MIN (64u * 1024u)

/* Strictest alignment this program actually needs. C99 has no max_align_t, so
 * it is probed. Floating-point types are deliberately absent: nothing here
 * ever stores one, so aligning for double would be alignment for data that
 * cannot exist. */
typedef struct { char c; void *p; } AlignProbePtr;
typedef struct { char c; long long l; } AlignProbeLL;
#define ALIGN_PTR offsetof(AlignProbePtr, p)
#define ALIGN_LL offsetof(AlignProbeLL, l)
#define MAX_ALIGN (ALIGN_PTR > ALIGN_LL ? ALIGN_PTR : ALIGN_LL)

typedef struct Block {
    struct Block *next;
    size_t used;
    size_t cap;
    unsigned char data[];
} Block;

struct Arena {
    Block *head;
};

Arena *arena_new(void) {
    Arena *a = calloc(1, sizeof *a);
    return a;
}

static Block *block_new(size_t need) {
    size_t cap = need > BLOCK_MIN ? need : BLOCK_MIN;
    Block *b;
    if (cap > SIZE_MAX - sizeof *b) return NULL;
    b = calloc(1, sizeof *b + cap);
    if (b == NULL) return NULL;
    b->cap = cap;
    b->used = 0;
    b->next = NULL;
    return b;
}

void *arena_alloc(Arena *a, size_t n, size_t align) {
    Block *b;
    size_t pad;

    if (a == NULL) return NULL;
    if (n == 0) n = 1;

    b = a->head;
    if (b != NULL) {
        pad = (align - (((uintptr_t)b->data + b->used) & (align - 1))) & (align - 1);
        if (pad <= b->cap - b->used && n <= b->cap - b->used - pad) {
            void *p = b->data + b->used + pad;
            b->used += pad + n;
            return p;
        }
    }

    /* A fresh block's data is at least scalar-aligned, so only the requested
     * alignment beyond that needs slack. */
    if (n > SIZE_MAX - align) return NULL;
    b = block_new(n + align);
    if (b == NULL) return NULL;
    b->next = a->head;
    a->head = b;

    pad = (align - ((uintptr_t)b->data & (align - 1))) & (align - 1);
    b->used = pad + n;
    return b->data + pad;
}

void *arena_array(Arena *a, size_t count, size_t size) {
    if (size != 0 && count > SIZE_MAX / size) return NULL;
    return arena_alloc(a, count * size, MAX_ALIGN);
}

char *arena_strndup(Arena *a, const char *s, size_t n) {
    char *p;
    if (n == SIZE_MAX) return NULL;
    p = arena_alloc(a, n + 1, 1);
    if (p == NULL) return NULL;
    if (n != 0) memcpy(p, s, n);
    p[n] = '\0';
    return p;
}

void arena_destroy(Arena *a) {
    Block *b;
    if (a == NULL) return;
    b = a->head;
    while (b != NULL) {
        Block *next = b->next;
        free(b);
        b = next;
    }
    free(a);
}
