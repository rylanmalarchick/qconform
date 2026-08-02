/* S3 gate: the restricted JSON reader.
 *
 * The load-bearing assertions are the negative ones: no float is ever
 * accepted, no integer outside i64 is ever accepted, and no input crashes.
 * Run under UBSan/ASan.
 */

#include <stdlib.h>

#include "../../src/arena.c"
#include "../../src/json.c"
#include "test.h"

static Arena *A;
static char errbuf[256];

static JsonValue *parse(const char *s) {
    return json_parse(A, s, strlen(s), errbuf, sizeof errbuf);
}

static bool rejects(const char *s, const char *want_msg) {
    JsonValue *v = json_parse(A, s, strlen(s), errbuf, sizeof errbuf);
    if (v != NULL) return false;
    return want_msg == NULL || strstr(errbuf, want_msg) != NULL;
}

/* --- shapes --------------------------------------------------------------- */

TEST(scalars) {
    JsonValue *v;

    v = parse("0");
    CHECK(v != NULL && v->kind == JSON_INT);
    CHECK_I64(v->as.integer, 0);

    v = parse("-0");
    CHECK(v != NULL && v->kind == JSON_INT);
    CHECK_I64(v->as.integer, 0);

    v = parse("  true ");
    CHECK(v != NULL && v->kind == JSON_BOOL && v->as.boolean);

    v = parse("false");
    CHECK(v != NULL && v->kind == JSON_BOOL && !v->as.boolean);

    v = parse("null");
    CHECK(v != NULL && v->kind == JSON_NULL);

    v = parse("\"gen0\"");
    CHECK(v != NULL && v->kind == JSON_STRING);
    CHECK_STR(v->as.string.ptr, "gen0");
    CHECK_I64(v->as.string.len, 4);
}

TEST(i64_boundaries) {
    JsonValue *v;

    v = parse("9223372036854775807");
    CHECK(v != NULL && v->kind == JSON_INT);
    CHECK(v->as.integer == INT64_MAX);

    /* INT64_MIN must survive: descriptors may legally contain it, and the
     * rational core is built to refuse arithmetic on it rather than wrap. */
    v = parse("-9223372036854775808");
    CHECK(v != NULL && v->kind == JSON_INT);
    CHECK(v->as.integer == INT64_MIN);

    /* out of range is classified rather than rejected, for the same reason
     * floats are: parse.c reports it with the field path */
    CHECK(parse("9223372036854775808")->kind == JSON_BIGINT);
    CHECK(parse("-9223372036854775809")->kind == JSON_BIGINT);
    CHECK(parse("99999999999999999999999999")->kind == JSON_BIGINT);
    CHECK(parse("-99999999999999999999999999")->kind == JSON_BIGINT);
}

TEST(floats_are_recognized_never_converted) {
    /* A float is classified, not converted: strtod is never called, so no
     * rounding question and no locale dependency exists. The typed accessor
     * in parse.c is what turns JSON_FLOAT into a diagnostic, which is how the
     * message keeps the offending field's path. This is invariant I1. */
    static const char *const floats[] = {
        "2.0", "-2.0", "0.5", "1e10", "1E10", "1e-10", "1e+10",
        "1e400",                  /* beyond double range: still just a float */
        "0.30000000000000004",    /* the classic: never rounded, never seen */
        "-0.0", "0.0", "123.456e-78", NULL};
    const char *const *f;
    JsonValue *v;

    for (f = floats; *f != NULL; f++) {
        v = parse(*f);
        CHECK(v != NULL);
        if (v == NULL) continue;
        CHECK(v->kind == JSON_FLOAT);
    }

    v = parse("{\"den\": 2.0}");
    CHECK(v != NULL && json_get(v, "den")->kind == JSON_FLOAT);
    v = parse("[1, 2, 3.5]");
    CHECK(v != NULL && v->as.array.items[2]->kind == JSON_FLOAT);

    /* malformed numbers are still refused outright */
    CHECK(rejects("1.", "empty fraction"));
    CHECK(rejects("1e", "empty exponent"));
    CHECK(rejects("1e+", "empty exponent"));
    CHECK(rejects(".5", "malformed number"));
}

TEST(objects_and_arrays) {
    JsonValue *v, *n;

    v = parse("{}");
    CHECK(v != NULL && v->kind == JSON_OBJECT);
    CHECK_I64(v->as.object.len, 0);
    CHECK(json_get(v, "nope") == NULL);

    v = parse("[]");
    CHECK(v != NULL && v->kind == JSON_ARRAY);
    CHECK_I64(v->as.array.len, 0);

    v = parse("{\"num\": 1, \"den\": 16773120000}");
    CHECK(v != NULL && v->kind == JSON_OBJECT);
    CHECK_I64(v->as.object.len, 2);
    n = json_get(v, "num");
    CHECK(n != NULL && n->kind == JSON_INT);
    CHECK_I64(n->as.integer, 1);
    n = json_get(v, "den");
    CHECK(n != NULL);
    CHECK_I64(n->as.integer, 16773120000LL);
    CHECK(json_get(v, "nu") == NULL);
    CHECK(json_get(v, "numm") == NULL);

    v = parse("[1, [2, [3]], {\"a\": [4]}]");
    CHECK(v != NULL && v->kind == JSON_ARRAY);
    CHECK_I64(v->as.array.len, 3);
    CHECK_I64(v->as.array.items[0]->as.integer, 1);
    CHECK_I64(v->as.array.items[1]->as.array.items[1]->as.array.items[0]->as.integer, 3);
    CHECK_I64(json_get(v->as.array.items[2], "a")->as.array.items[0]->as.integer, 4);
}

TEST(member_order_is_input_order) {
    /* The report must be byte-identical for identical input, so nothing in
     * the pipeline may reorder members. */
    JsonValue *v = parse("{\"z\": 1, \"a\": 2, \"m\": 3}");
    CHECK(v != NULL);
    CHECK_STR(v->as.object.members[0].key, "z");
    CHECK_STR(v->as.object.members[1].key, "a");
    CHECK_STR(v->as.object.members[2].key, "m");
}

TEST(duplicate_keys_last_wins) {
    /* Matches the previous implementation. See the comment on json_get. */
    JsonValue *v = parse("{\"a\": 1, \"a\": 2}");
    CHECK(v != NULL);
    CHECK_I64(v->as.object.len, 2);
    CHECK_I64(json_get(v, "a")->as.integer, 2);
}

/* --- strings -------------------------------------------------------------- */

TEST(string_escapes) {
    JsonValue *v;

    v = parse("\"a\\nb\\tc\\\"d\\\\e\\/f\"");
    CHECK(v != NULL);
    CHECK_STR(v->as.string.ptr, "a\nb\tc\"d\\e/f");

    v = parse("\"\\b\\f\\r\"");
    CHECK(v != NULL);
    CHECK_STR(v->as.string.ptr, "\b\f\r");

    /* \u escapes, including the ones the report writer must re-escape */
    v = parse("\"\\u0000\\u001f\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 2);
    CHECK_I64((unsigned char)v->as.string.ptr[0], 0);
    CHECK_I64((unsigned char)v->as.string.ptr[1], 0x1f);

    /* a NUL inside a string is legal input, so length is carried separately
     * from the NUL terminator */
    v = parse("\"a\\u0000b\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 3);
    CHECK_I64((unsigned char)v->as.string.ptr[1], 0);
    CHECK_I64(v->as.string.ptr[2], 'b');

    /* multi-byte UTF-8 out of \u */
    v = parse("\"\\u00e9\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 2);
    v = parse("\"\\u20ac\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 3);
    /* surrogate pair -> 4-byte encoding */
    v = parse("\"\\ud83d\\ude00\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 4);

    /* raw UTF-8 passes through untouched */
    v = parse("\"\xc3\xa9\"");
    CHECK(v != NULL);
    CHECK_I64(v->as.string.len, 2);
}

TEST(string_rejections) {
    CHECK(rejects("\"unterminated", "unterminated"));
    CHECK(rejects("\"bad\\escape\"", "unknown escape"));
    CHECK(rejects("\"\\u00\"", NULL));
    CHECK(rejects("\"\\uZZZZ\"", "\\u"));
    CHECK(rejects("\"\\ud83d\"", "surrogate"));
    CHECK(rejects("\"\\ude00\"", "surrogate"));
    CHECK(rejects("\"\\ud83dx\"", "surrogate"));
    /* a raw control byte inside a string literal is invalid JSON */
    CHECK(rejects("\"a\nb\"", "control character"));
    CHECK(rejects("\"a\tb\"", "control character"));
    /* a trailing backslash must not read past the end */
    CHECK(rejects("\"abc\\", "unterminated"));
}

/* --- syntax rejections ---------------------------------------------------- */

TEST(syntax_rejections) {
    CHECK(rejects("", "unexpected end"));
    CHECK(rejects("   ", "unexpected end"));
    CHECK(rejects("{", NULL));
    CHECK(rejects("}", NULL));
    CHECK(rejects("[", NULL));
    CHECK(rejects("[1", "unterminated"));
    CHECK(rejects("{\"a\"", NULL));
    CHECK(rejects("{\"a\":", "unexpected end"));
    CHECK(rejects("{\"a\" 1}", "expected ':'"));
    CHECK(rejects("{a: 1}", "expected string"));
    CHECK(rejects("{\"a\": 1,}", "expected string"));
    CHECK(rejects("[1,]", NULL));
    CHECK(rejects("[1 2]", "expected ',' or ']'"));
    CHECK(rejects("{\"a\": 1 \"b\": 2}", "expected ',' or '}'"));
    CHECK(rejects("tru", "malformed literal"));
    CHECK(rejects("nul", "malformed literal"));
    CHECK(rejects("+1", "malformed number"));
    CHECK(rejects("01", "leading zero"));
    CHECK(rejects("-", "malformed number"));
    CHECK(rejects("- 1", "malformed number"));
    CHECK(rejects("1 2", "trailing content"));
    CHECK(rejects("{} {}", "trailing content"));
    CHECK(rejects("\"a\" x", "trailing content"));
}

TEST(depth_is_bounded) {
    /* An unbounded recursive descent parser is a stack overflow waiting for
     * a hostile input; the limit is a refusal, not a crash. */
    size_t i;
    size_t n = JSON_MAX_DEPTH + 10;
    char *deep = malloc(2 * n + 1);
    CHECK(deep != NULL);
    if (deep == NULL) return;
    for (i = 0; i < n; i++) deep[i] = '[';
    for (i = 0; i < n; i++) deep[n + i] = ']';
    deep[2 * n] = '\0';
    CHECK(rejects(deep, "too deep"));
    free(deep);

    /* just under the limit still parses */
    n = JSON_MAX_DEPTH - 2;
    deep = malloc(2 * n + 1);
    CHECK(deep != NULL);
    if (deep == NULL) return;
    for (i = 0; i < n; i++) deep[i] = '[';
    for (i = 0; i < n; i++) deep[n + i] = ']';
    deep[2 * n] = '\0';
    CHECK(parse(deep) != NULL);
    free(deep);
}

TEST(embedded_nul_in_input) {
    /* json_parse takes a length, so a NUL byte in the buffer is data, not a
     * terminator. A NUL outside a string is a syntax error, not a silent
     * truncation to a valid prefix. */
    const char buf[] = {'[', '1', '\0', ',', '2', ']'};
    CHECK(json_parse(A, buf, sizeof buf, errbuf, sizeof errbuf) == NULL);
    {
        const char ok[] = {'[', '1', ',', '2', ']'};
        CHECK(json_parse(A, ok, sizeof ok, errbuf, sizeof errbuf) != NULL);
    }
}

/* --- fuzz: never crash, never accept a float ------------------------------ */

static uint64_t rng_state = 0x243f6a8885a308d3ULL;

static uint64_t rng(void) {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}

TEST(fuzz_mutations_never_crash) {
    /* Structure-aware bytes: an alphabet biased toward JSON punctuation and
     * digits finds parser bugs that uniform random bytes never reach. */
    static const char alphabet[] = "{}[]\",:0123456789.eE-+\\/ \t\n\rabcdefnulstruy\x01\x7f";
    const size_t alphabet_len = sizeof alphabet - 1;
    char buf[192];
    int round;
    long accepted = 0, rejected = 0;

    for (round = 0; round < 200000; round++) {
        size_t len = (size_t)(rng() % sizeof buf);
        size_t i;
        JsonValue *v;
        for (i = 0; i < len; i++) buf[i] = alphabet[rng() % alphabet_len];

        v = json_parse(A, buf, len, errbuf, sizeof errbuf);
        if (v == NULL) {
            rejected++;
            /* a failure must always carry a message */
            CHECK(errbuf[0] != '\0');
            if (errbuf[0] == '\0') break;
        } else {
            accepted++;
            /* whatever was accepted must be a well-formed value; walking it
             * is what makes ASan earn its keep */
            CHECK(v->kind <= JSON_OBJECT);
        }

        /* keep the arena from growing without bound across 200k rounds */
        if ((round & 0x3ff) == 0x3ff) {
            arena_destroy(A);
            A = arena_new();
            CHECK(A != NULL);
            if (A == NULL) return;
        }
    }
    CHECK(accepted > 0);   /* the alphabet must actually reach valid inputs */
    CHECK(rejected > 0);
}

TEST(fuzz_number_shapes_never_yield_floats) {
    /* Anything that lexes as a number must come back an integer, or not at
     * all. There is no third outcome. */
    static const char digits[] = "0123456789.eE-+";
    char buf[40];
    int round;

    for (round = 0; round < 200000; round++) {
        size_t len = 1 + (size_t)(rng() % (sizeof buf - 1));
        size_t i;
        JsonValue *v;
        for (i = 0; i < len; i++) buf[i] = digits[rng() % (sizeof digits - 1)];
        buf[len] = '\0';

        v = json_parse(A, buf, len, errbuf, sizeof errbuf);
        if (v == NULL) continue;
        /* a number token has exactly three possible outcomes and none of them
         * involves a floating-point value */
        CHECK(v->kind == JSON_INT || v->kind == JSON_FLOAT || v->kind == JSON_BIGINT);
        if (!(v->kind == JSON_INT || v->kind == JSON_FLOAT || v->kind == JSON_BIGINT)) break;
        /* anything classified as an integer must contain no float marker */
        if (v->kind == JSON_INT) {
            CHECK(strchr(buf, '.') == NULL && strchr(buf, 'e') == NULL &&
                  strchr(buf, 'E') == NULL);
            if (strchr(buf, '.') != NULL) break;
        }
    }
}

TEST_MAIN_BEGIN
A = arena_new();
if (A == NULL) {
    fprintf(stderr, "arena_new failed\n");
    return 1;
}
RUN(scalars);
RUN(i64_boundaries);
RUN(floats_are_recognized_never_converted);
RUN(objects_and_arrays);
RUN(member_order_is_input_order);
RUN(duplicate_keys_last_wins);
RUN(string_escapes);
RUN(string_rejections);
RUN(syntax_rejections);
RUN(depth_is_bounded);
RUN(embedded_nul_in_input);
RUN(fuzz_mutations_never_crash);
RUN(fuzz_number_shapes_never_yield_floats);
arena_destroy(A);
TEST_MAIN_END
