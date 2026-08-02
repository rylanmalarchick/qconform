#ifndef QCONFORM_TEST_H
#define QCONFORM_TEST_H

/* Minimal assert harness. Test files #include the .c files under test
 * directly, so file-private (static) functions are reachable — the same
 * access the previous implementation's test blocks had. */

#include <stdio.h>
#include <string.h>

extern int qc_test_failures;
extern const char *qc_test_name;

#define TEST(name)                                     \
    static void name(void);                            \
    static void run_##name(void) {                     \
        qc_test_name = #name;                          \
        name();                                        \
    }                                                  \
    static void name(void)

#define RUN(name) run_##name()

#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            fprintf(stderr, "FAIL %s:%d [%s]: %s\n", __FILE__, __LINE__,  \
                    qc_test_name, #cond);                                 \
            qc_test_failures++;                                           \
        }                                                                 \
    } while (0)

#define CHECK_I64(got, want)                                                  \
    do {                                                                      \
        long long g_ = (long long)(got), w_ = (long long)(want);              \
        if (g_ != w_) {                                                       \
            fprintf(stderr, "FAIL %s:%d [%s]: %s == %lld, wanted %lld\n",     \
                    __FILE__, __LINE__, qc_test_name, #got, g_, w_);          \
            qc_test_failures++;                                               \
        }                                                                     \
    } while (0)

#define CHECK_STR(got, want)                                                  \
    do {                                                                      \
        const char *g_ = (got), *w_ = (want);                                 \
        if (g_ == NULL || strcmp(g_, w_) != 0) {                              \
            fprintf(stderr, "FAIL %s:%d [%s]: %s == \"%s\", wanted \"%s\"\n", \
                    __FILE__, __LINE__, qc_test_name, #got,                   \
                    g_ ? g_ : "(null)", w_);                                  \
            qc_test_failures++;                                               \
        }                                                                     \
    } while (0)

#define TEST_MAIN_BEGIN            \
    int qc_test_failures = 0;      \
    const char *qc_test_name = ""; \
    int main(void) {

#define TEST_MAIN_END                                             \
    if (qc_test_failures != 0) {                                  \
        fprintf(stderr, "%d check(s) failed\n", qc_test_failures); \
        return 1;                                                 \
    }                                                             \
    printf("ok\n");                                               \
    return 0;                                                     \
    }

#endif /* QCONFORM_TEST_H */
