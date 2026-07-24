#ifndef QCONFORM_CHECK_H
#define QCONFORM_CHECK_H

/* The checker: descriptor validation, resolve, rule interpreters, report.
 *
 * Unrealizable is a normal result and comes back as Rejection data. A false
 * return is reserved for tool errors — an invalid descriptor, an unbound
 * channel, allocation failure, or arithmetic overflow. Overflow in particular
 * is never allowed to become a verdict: a checker that wraps silently answers
 * the wrong question.
 */

#include <stdbool.h>
#include <stdint.h>

#include "arena.h"
#include "capability.h"
#include "ir.h"
#include "parse.h"

typedef struct {
    CoverageClass rule;
    Severity severity;
    RepairId repair;
    bool has_repair;
    int64_t element;
    Quantity quantity;
    Rat value;
    Rat limit;
} Rejection;

typedef struct {
    BudgetId id;
    BudgetVerdict verdict;
    int64_t lower;
    int64_t upper;
    int64_t limit;
} BudgetResult;

typedef struct {
    Identification ident;
    Verdict verdict;
    const Rejection *rejections;
    size_t n_rejections;
    const BudgetResult *budgets;
    size_t n_budgets;
    CoverageStatus coverage[QC_COVERAGE_CLASS_COUNT];
} Report;

/* One up-front pass over the descriptor. Any failure is a tool error naming
 * the descriptor: never a pass, and never a rejection. */
bool validate_descriptor(const Descriptor *d, Diag *diag);

bool check(Arena *a, const IrProgram *prog, const Descriptor *desc, Report *out, Diag *diag);

#endif /* QCONFORM_CHECK_H */
