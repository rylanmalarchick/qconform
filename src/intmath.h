#ifndef QCONFORM_INTMATH_H
#define QCONFORM_INTMATH_H

/* Signed integer primitives with the semantics the checker was written
 * against. Two of these exist because C's defaults differ from Zig's in ways
 * that produce a wrong verdict rather than a crash:
 *
 *   abs_i64  - Zig's @abs on i64 yields u64, so INT64_MIN is representable.
 *              llabs(INT64_MIN) is undefined, and at -O2 the compiler is
 *              entitled to delete any overflow check that depends on it.
 *              A descriptor may legally contain {"num": INT64_MIN, "den": 1}.
 *
 *   floor_div / floor_mod
 *            - Zig's @divFloor/@mod round toward negative infinity and yield
 *              a non-negative remainder. C's / and % truncate toward zero and
 *              give the remainder the dividend's sign. For -7/2 that is
 *              (-4, 1) against (-3, -1), which changes which branch
 *              round_nearest_even takes.
 *
 * Bare / and % on signed values are banned in check.c; see the tripwire in
 * the Makefile's `check` target.
 */

#include <stdint.h>

#if !defined(__SIZEOF_INT128__)
#error "qconform requires 128-bit integers (__int128): GCC, Clang, or ICC. MSVC users: clang-cl."
#endif

typedef __int128 i128;
typedef unsigned __int128 u128;

/* |x| without the INT64_MIN trap: negate in the unsigned domain. */
static inline uint64_t abs_i64(int64_t x) {
    return x < 0 ? (uint64_t)(-(x + 1)) + 1 : (uint64_t)x;
}

static inline u128 abs_i128(i128 x) {
    return x < 0 ? (u128)(-(x + 1)) + 1 : (u128)x;
}

/* Quotient rounded toward negative infinity. b must be nonzero; a == INT64_MIN
 * with b == -1 overflows and is the caller's responsibility (the checker only
 * ever divides by positive denominators and grids). */
static inline int64_t floor_div(int64_t a, int64_t b) {
    int64_t q = a / b;
    if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
    return q;
}

/* Remainder matching floor_div: same sign as the divisor, so it is
 * non-negative for the positive divisors the checker uses. */
static inline int64_t floor_mod(int64_t a, int64_t b) {
    int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) r += b;
    return r;
}

/* Greatest common divisor of two unsigned values. gcd(0, n) == n. */
static inline u128 gcd_u128(u128 a, u128 b) {
    while (b != 0) {
        u128 t = a % b;
        a = b;
        b = t;
    }
    return a;
}

/* a + b into *out; returns 1 on overflow, leaving *out unspecified. */
static inline int add_overflow_i64(int64_t a, int64_t b, int64_t *out) {
    return __builtin_add_overflow(a, b, out);
}

#endif /* QCONFORM_INTMATH_H */
