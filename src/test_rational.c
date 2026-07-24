/* S2 gate: the rational core and the two porting hazards.
 *
 * The first six tests are the Zig rational.zig tests transliterated. The rest
 * exist because C's defaults differ from Zig's in ways that would produce a
 * wrong verdict rather than a crash, and because -O2 is entitled to delete an
 * overflow check that depends on undefined behavior. Run this at -O0, -O2,
 * and under UBSan/ASan; -O2 is the run that matters for abs_i64.
 */

#include "arena.c"
#include "rational.c"
#include "test.h"

static Rat rat(int64_t n, int64_t d) {
    Rat r = {n, d};
    return r;
}

/* --- transliterated from rational.zig ------------------------------------ */

TEST(canonical) {
    CHECK(rat_is_canonical(rat(1, 2)));
    CHECK(rat_is_canonical(rat(-3, 7)));
    CHECK(!rat_is_canonical(rat(2, 4)));
    CHECK(!rat_is_canonical(rat(1, 0)));
    CHECK(!rat_is_canonical(rat(1, -2)));
    CHECK(rat_is_canonical(RAT_ZERO));
}

TEST(cmp_cross_mult) {
    CHECK_I64(rat_cmp(rat(1, 3), rat(1, 2)), -1);
    CHECK_I64(rat_cmp(rat(1, 3), rat(1, 3)), 0);
    /* near-overflow operands survive via 128-bit cross multiplication */
    CHECK_I64(rat_cmp(rat(INT64_MAX, 1), rat(1, INT64_MAX)), 1);
}

TEST(mul_reduces) {
    Rat out;
    CHECK(rat_mul(rat(2, 3), rat(3, 4), &out));
    CHECK(rat_eq(out, rat(1, 2)));
    CHECK(rat_mul(rat(-2, 3), rat(3, 4), &out));
    CHECK(rat_eq(out, rat(-1, 2)));
}

TEST(exact_div) {
    /* 28/39 grids: one fabric cycle over one dt unit */
    int64_t q;
    bool exact;
    CHECK(rat_exact_div(rat(25, 14976000000), rat(1, 16773120000), &q, &exact));
    CHECK(exact);
    CHECK_I64(q, 28);
    CHECK(rat_exact_div(rat(25, 10752000000), rat(1, 16773120000), &q, &exact));
    CHECK(exact);
    CHECK_I64(q, 39);
    CHECK(rat_exact_div(rat(1, 1000000000), rat(1, 16773120000), &q, &exact));
    CHECK(!exact);
}

TEST(exact_mul_int) {
    int64_t p;
    bool exact;
    CHECK(rat_exact_mul_int(rat(1, 3), 6, &p, &exact));
    CHECK(exact);
    CHECK_I64(p, 2);
    CHECK(rat_exact_mul_int(rat(1, 3), 7, &p, &exact));
    CHECK(!exact);
}

TEST(overflow_detected) {
    Rat out;
    CHECK(!rat_mul(rat(INT64_MAX, 1), rat(INT64_MAX, 1), &out));
    CHECK(!rat_div(rat(INT64_MAX, 1), RAT_ZERO, &out));
}

/* --- H1: floored vs truncated division ----------------------------------- */

TEST(h1_floor_helpers_are_not_c_defaults) {
    /* This is the whole hazard in four lines: C's / and % disagree with the
     * semantics check.zig was written against, on exactly the negative
     * operands a bad time base makes reachable. */
    CHECK_I64(floor_div(-7, 2), -4);
    CHECK_I64(floor_mod(-7, 2), 1);
    CHECK_I64(-7 / 2, -3);
    CHECK_I64(-7 % 2, -1);

    CHECK_I64(floor_div(7, 2), 3);
    CHECK_I64(floor_mod(7, 2), 1);
    CHECK_I64(floor_div(-8, 2), -4);
    CHECK_I64(floor_mod(-8, 2), 0);
    /* floor_mod takes the divisor's sign, so it is non-negative for the
     * positive divisors the checker uses */
    CHECK(floor_mod(-1, 39) >= 0);
    CHECK_I64(floor_mod(-1, 39), 38);
}

TEST(h1_round_nearest_even_on_negatives) {
    /* -7/2 = -3.5, ties to even -> -4. With truncating division this comes
     * out -3: the wrong branch, silently. */
    CHECK_I64(rat_round_nearest_even(rat(-7, 2)), -4);
    CHECK_I64(rat_round_nearest_even(rat(-5, 2)), -2);
    CHECK_I64(rat_round_nearest_even(rat(7, 2)), 4);
    CHECK_I64(rat_round_nearest_even(rat(5, 2)), 2);
    /* non-ties round to nearest in both directions */
    CHECK_I64(rat_round_nearest_even(rat(-2, 3)), -1);
    CHECK_I64(rat_round_nearest_even(rat(2, 3)), 1);
    CHECK_I64(rat_round_nearest_even(rat(-1, 3)), 0);
    CHECK_I64(rat_round_nearest_even(rat(1, 3)), 0);
    /* integers are fixed points */
    CHECK_I64(rat_round_nearest_even(rat(-4, 1)), -4);
    CHECK_I64(rat_round_nearest_even(RAT_ZERO), 0);
}

/* --- H2: absolute value at INT64_MIN ------------------------------------- */

TEST(h2_abs_i64_at_int64_min) {
    /* llabs(INT64_MIN) is undefined; this must be exactly 2^63. */
    CHECK(abs_i64(INT64_MIN) == (uint64_t)1 << 63);
    CHECK(abs_i64(INT64_MAX) == (uint64_t)INT64_MAX);
    CHECK(abs_i64(-1) == 1);
    CHECK(abs_i64(0) == 0);
}

TEST(h2_abs_i128_at_min) {
    i128 min128 = -(((i128)1) << 126) - (((i128)1) << 126);
    CHECK(abs_i128(min128) == ((u128)1 << 127));
    CHECK(abs_i128((i128)-5) == 5);
}

TEST(h2_int64_min_through_is_canonical) {
    /* {"num": -9223372036854775808, "den": 1} is accepted by the parser, so
     * this path is reachable from a descriptor file. */
    CHECK(rat_is_canonical(rat(INT64_MIN, 1)));
    CHECK(!rat_is_canonical(rat(INT64_MIN, 2)));  /* both even */
}

TEST(h2_int64_min_through_reduce) {
    Rat out;
    /* reduce() normalizes through the unsigned magnitude and only then casts
     * back, so |INT64_MIN| == 2^63 does not fit and every arithmetic entry
     * point refuses it. Verified to match rational.zig, which casts the same
     * way (std.math.cast(i64, un / g) orelse error.Overflow).
     *
     * The useful consequence: INT64_MIN is accepted by the parser as a
     * canonical numerator but can never be computed with, so it cannot wrap
     * into a wrong verdict — it can only become a tool error. */
    CHECK(!rat_mul(rat(INT64_MIN, 1), rat(1, 1), &out));
    CHECK(!rat_mul_int(rat(INT64_MIN, 1), 2, &out));
    CHECK(!rat_div(rat(INT64_MIN, 1), rat(-1, 1), &out));
    CHECK(!rat_add(rat(INT64_MIN, 1), RAT_ZERO, &out));

    /* comparison does not reduce, so it stays exact and total */
    CHECK(rat_cmp(rat(INT64_MIN, 1), RAT_ZERO) == -1);
    CHECK(rat_cmp(rat(INT64_MIN, 1), rat(INT64_MIN, 1)) == 0);
}

TEST(h2_int64_min_round) {
    /* floor_div(INT64_MIN, 1) is fine; the -1 divisor case is excluded by
     * canonical form (den > 0) so it cannot arise from a parsed rational. */
    CHECK_I64(rat_round_nearest_even(rat(INT64_MIN, 1)), INT64_MIN);
}

/* --- overflow refused at every entry point -------------------------------- */

TEST(overflow_at_every_entry) {
    Rat out;
    Rat big = rat(INT64_MAX, 1);
    Rat tiny = rat(1, INT64_MAX);
    int64_t q;
    bool exact;

    CHECK(!rat_mul(big, big, &out));
    CHECK(!rat_mul_int(big, INT64_MAX, &out));
    CHECK(!rat_div(big, tiny, &out));
    CHECK(!rat_add(big, big, &out));
    CHECK(!rat_exact_div(big, tiny, &q, &exact));
    CHECK(!rat_exact_mul_int(big, INT64_MAX, &q, &exact));

    /* denominators overflow too, not just numerators */
    CHECK(!rat_mul(rat(1, INT64_MAX), rat(1, INT64_MAX - 2), &out));

    /* division by zero is refused before any arithmetic */
    CHECK(!rat_div(big, RAT_ZERO, &out));
    CHECK(!rat_exact_div(big, RAT_ZERO, &q, &exact));
}

TEST(add_matches_check_zig_semantics) {
    Rat out;
    CHECK(rat_add(rat(1, 2), rat(1, 3), &out));
    CHECK(rat_eq(out, rat(5, 6)));
    /* sums that cancel land on canonical zero, not 0/n */
    CHECK(rat_add(rat(1, 2), rat(-1, 2), &out));
    CHECK(rat_eq(out, RAT_ZERO));
    /* result is reduced */
    CHECK(rat_add(rat(1, 6), rat(1, 3), &out));
    CHECK(rat_eq(out, rat(1, 2)));
    /* phase accumulation in turns, the real use */
    CHECK(rat_add(rat(1, 4), rat(1, 4), &out));
    CHECK(rat_eq(out, rat(1, 2)));
}

/* --- arena ---------------------------------------------------------------- */

TEST(arena_basics) {
    Arena *a = arena_new();
    unsigned char *p;
    int64_t *arr;
    char *s;
    size_t i;

    CHECK(a != NULL);

    p = arena_alloc(a, 1, 1);
    CHECK(p != NULL);
    CHECK_I64(p[0], 0); /* zeroed */

    arr = arena_array(a, 1000, sizeof *arr);
    CHECK(arr != NULL);
    CHECK(((uintptr_t)arr % sizeof(void *)) == 0);
    for (i = 0; i < 1000; i++) CHECK_I64(arr[i], 0);
    for (i = 0; i < 1000; i++) arr[i] = (int64_t)i;
    CHECK_I64(arr[999], 999);

    s = arena_strndup(a, "gen0-and-more", 4);
    CHECK_STR(s, "gen0");

    /* allocation larger than a block still succeeds */
    p = arena_alloc(a, 1024u * 1024u, 16);
    CHECK(p != NULL);
    CHECK(((uintptr_t)p % 16) == 0);

    /* count * size overflow is refused, not wrapped */
    CHECK(arena_array(a, SIZE_MAX / 4, 8) == NULL);

    arena_destroy(a);
}

TEST(arena_many_small_allocations) {
    Arena *a = arena_new();
    size_t i;
    for (i = 0; i < 100000; i++) {
        void *p = arena_alloc(a, 24, 8);
        CHECK(p != NULL);
        if (p == NULL) break;
        CHECK(((uintptr_t)p % 8) == 0);
    }
    arena_destroy(a);
}

TEST_MAIN_BEGIN
RUN(canonical);
RUN(cmp_cross_mult);
RUN(mul_reduces);
RUN(exact_div);
RUN(exact_mul_int);
RUN(overflow_detected);
RUN(h1_floor_helpers_are_not_c_defaults);
RUN(h1_round_nearest_even_on_negatives);
RUN(h2_abs_i64_at_int64_min);
RUN(h2_abs_i128_at_min);
RUN(h2_int64_min_through_is_canonical);
RUN(h2_int64_min_through_reduce);
RUN(h2_int64_min_round);
RUN(overflow_at_every_entry);
RUN(add_matches_check_zig_semantics);
RUN(arena_basics);
RUN(arena_many_small_allocations);
TEST_MAIN_END
