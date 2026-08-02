#ifndef QCONFORM_ARENA_H
#define QCONFORM_ARENA_H

/* Bump allocator over malloc'd blocks.
 *
 * The whole program is arena-allocated and nothing is individually freed: the
 * process is short-lived and every allocation lives until it exits. This is
 * not a shortcut, it is the memory model the checker was written against, and
 * it is why there is no ownership graph to get wrong. Do not add free() calls
 * for individual allocations.
 *
 * arena_alloc returns NULL when the underlying malloc fails; callers treat
 * that as a tool error (exit 3), never as a verdict.
 */

#include <stddef.h>

typedef struct Arena Arena;

Arena *arena_new(void);

/* n bytes aligned to `align` (a power of two). Returns NULL on failure or if
 * the size computation would overflow. Memory is zeroed. */
void *arena_alloc(Arena *a, size_t n, size_t align);

/* count elements of `size` bytes each, aligned for any scalar. NULL on
 * failure or on count * size overflow. */
void *arena_array(Arena *a, size_t count, size_t size);

/* Release every block. Called once, at exit. */
void arena_destroy(Arena *a);

#endif /* QCONFORM_ARENA_H */
