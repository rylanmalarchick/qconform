#include "check.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Two C defaults are wrong for this file and never appear in it. Bare / and %
 * on signed values truncate toward zero. This checker rounds toward negative
 * infinity, so it uses floor_div and floor_mod from intmath.h. llabs is
 * undefined at INT64_MIN, so it uses abs_i64.
 *
 * Both defaults differ only on negative operands. That is where a wrong answer
 * is silent, so the difference matters more than its size suggests.
 *
 * This file was ported from a Zig implementation, statement for statement.
 * That implementation is archived at
 * desktop:/mnt/four/archive/qconform-zig-2026-07-24/. */

typedef struct {
    Rejection rej;
    uint32_t seq;
} SinkEntry;

typedef struct {
    SinkEntry *items;
    size_t len;
    size_t cap;
    uint32_t seq;
    Arena *arena;
    bool oom;
} Sink;

static bool sink_add(Sink *s, Rejection r) {
    if (s->len == s->cap) {
        size_t cap = s->cap == 0 ? 16 : s->cap * 2;
        SinkEntry *items = arena_array(s->arena, cap, sizeof *items);
        if (items == NULL) {
            s->oom = true;
            return false;
        }
        if (s->len != 0) memcpy(items, s->items, s->len * sizeof *items);
        s->items = items;
        s->cap = cap;
    }
    s->items[s->len].rej = r;
    s->items[s->len].seq = s->seq++;
    s->len++;
    return true;
}

/* Shared state for one check() run. Without it each rule interpreter would
 * take a dozen parameters. */
typedef struct {
    Arena *arena;
    const IrProgram *prog;
    const Descriptor *desc;
    const uint32_t *bind;   /* program channel -> descriptor channel */
    const Rat *factor;      /* program unit / descriptor unit */
    Sink sink;
    CoverageStatus coverage[QC_COVERAGE_CLASS_COUNT];
    Diag *diag;
} Check;

static bool tool_error(Check *k, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(k->diag->buf, sizeof k->diag->buf, fmt, ap);
    va_end(ap);
    return false;
}

static void cover(Check *k, CoverageClass c, CoverageStatus s) { k->coverage[c] = s; }

/* Which severities a rule can carry, and whether a descriptor may declare it.
 *
 * A rejection carries a repair id when, and only when, its severity is
 * vendor_repairable. See documentation/report-format-v0.txt. Each rule decides
 * that at the point it emits, and most rules decide it once rather than per
 * descriptor. A descriptor that assigns a severity the rule cannot express
 * would produce a report that breaks the format it claims to follow.
 *
 * Some rule ids are emitted by the checker but are never read back as a
 * descriptor constraint. frequency_resolution and amplitude_resolution are
 * emitted from the resolution field of the frequency_range and amplitude_range
 * constraints. Declaring one directly does nothing. The checker refuses it
 * rather than accept a constraint it will ignore. */
typedef enum {
    SEV_FATAL_ONLY,        /* the emit site never sets a repair */
    SEV_REPAIRABLE_ONLY,   /* the emit site always sets a repair */
    SEV_EITHER,            /* the emit site follows the declared severity */
    SEV_NOT_DECLARABLE     /* the checker owns this rule; a descriptor may not declare it */
} SeverityRule;

static SeverityRule severity_rule_for(RuleId id) {
    switch (id) {
    case QC_RULE_pulse_length_range:
    case QC_RULE_readout_length_range:
    case QC_RULE_envelope_sample_grid:
    case QC_RULE_envelope_amplitude:
        return SEV_FATAL_ONLY;
    case QC_RULE_pulse_length_grid:
    case QC_RULE_phase_resolution:
        return SEV_REPAIRABLE_ONLY;
    case QC_RULE_frequency_range:
    case QC_RULE_amplitude_range:
        return SEV_EITHER;
    default:
        return SEV_NOT_DECLARABLE;
    }
}

bool validate_descriptor(const Descriptor *d, Diag *diag) {
    size_t i, j;
    for (i = 0; i < d->n_channels; i++) {
        const CapChannel *ch = &d->channels[i];
        if (ch->unit.num <= 0) {
            diag_set(diag, "descriptor channel '" STR_FMT "': non-positive unit", STR_ARG(ch->name));
            return false;
        }
        if (ch->duration_grid <= 0 || ch->schedule_grid <= 0) {
            diag_set(diag, "descriptor channel '" STR_FMT "': non-positive grid", STR_ARG(ch->name));
            return false;
        }
        for (j = 0; j < ch->n_constraints; j++) {
            const Constraint *c = &ch->constraints[j];
            bool ok;
            switch (c->shape) {
            case QC_SHAPE_range_units:
                ok = c->has_min_units || c->has_max_units || c->id == QC_RULE_pulse_length_grid;
                break;
            case QC_SHAPE_range_resolution:
                ok = c->has_min || c->has_max || c->has_resolution;
                break;
            case QC_SHAPE_grid_samples:
                ok = c->has_grid && c->grid > 0;
                break;
            default: ok = false; break;
            }
            if (!ok) {
                diag_set(diag,
                         "descriptor channel '" STR_FMT "' constraint %s: shape %s missing its parameters",
                         STR_ARG(ch->name), rule_id_name(c->id), shape_name(c->shape));
                return false;
            }

            switch (severity_rule_for(c->id)) {
            case SEV_NOT_DECLARABLE:
                diag_set(diag,
                         "descriptor channel '" STR_FMT "' constraint %s: the checker never reads this rule as a constraint",
                         STR_ARG(ch->name), rule_id_name(c->id));
                return false;
            case SEV_FATAL_ONLY:
                if (c->severity != QC_SEV_fatal) {
                    diag_set(diag,
                             "descriptor channel '" STR_FMT "' constraint %s: severity must be fatal, this rule reports no repair",
                             STR_ARG(ch->name), rule_id_name(c->id));
                    return false;
                }
                break;
            case SEV_REPAIRABLE_ONLY:
                if (c->severity != QC_SEV_vendor_repairable) {
                    diag_set(diag,
                             "descriptor channel '" STR_FMT "' constraint %s: severity must be vendor_repairable, this rule always reports a repair",
                             STR_ARG(ch->name), rule_id_name(c->id));
                    return false;
                }
                break;
            case SEV_EITHER:
                break;
            }
        }
        if (ch->capabilities.has_envelope_sample_grid && ch->capabilities.envelope_sample_grid <= 0) {
            diag_set(diag, "descriptor channel '" STR_FMT "': non-positive envelope_sample_grid",
                     STR_ARG(ch->name));
            return false;
        }
    }
    for (i = 0; i < d->n_budgets; i++) {
        if (d->budgets[i].limit < 0) {
            diag_set(diag, "descriptor budget %s: negative limit", budget_id_name(d->budgets[i].id));
            return false;
        }
    }
    return true;
}

static const Constraint *find_constraint(const CapChannel *dc, RuleId id) {
    size_t i;
    for (i = 0; i < dc->n_constraints; i++)
        if (dc->constraints[i].id == id) return &dc->constraints[i];
    return NULL;
}

typedef struct {
    int64_t clock; /* descriptor channel units */
    Rat frequency;
    Rat phase;
    bool freq_checked;
    bool phase_checked;
} FrameState;

/* value * unit, where any overflow is a tool error rather than a wrong
 * verdict. */
static bool scale(Check *k, Rat unit, int64_t n, Rat *out) {
    if (!rat_mul_int(unit, n, out)) return tool_error(k, "arithmetic overflow (value * unit)");
    return true;
}

static const CapChannel *desc_channel_of_frame(Check *k, uint32_t frame) {
    return &k->desc->channels[k->bind[k->prog->frames[frame].channel]];
}

static bool check_frequency(Check *k, uint32_t frame, Rat freq, int64_t element) {
    const CapChannel *dc = desc_channel_of_frame(k, frame);
    const Constraint *c = find_constraint(dc, QC_RULE_frequency_range);
    bool below, above;

    if (c == NULL) return true;
    cover(k, QC_COV_frequency_range, QC_CSTAT_checked);
    below = c->has_min && rat_cmp(freq, c->min) < 0;
    above = c->has_max && rat_cmp(freq, c->max) > 0;
    if (below || above) {
        Rejection r;
        r.rule = QC_COV_frequency_range;
        r.severity = c->severity;
        r.has_repair = (c->severity == QC_SEV_vendor_repairable);
        r.repair = QC_REPAIR_alias_mod_f_dds;
        r.element = element;
        r.quantity = QC_QTY_frequency;
        r.value = freq;
        r.limit = below ? c->min : c->max;
        if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
    }
    if (c->has_resolution) {
        int64_t q;
        bool exact;
        cover(k, QC_COV_frequency_resolution, QC_CSTAT_checked);
        if (!rat_exact_div(freq, c->resolution, &q, &exact))
            return tool_error(k, "arithmetic overflow (frequency resolution)");
        if (!exact) {
            Rejection r;
            r.rule = QC_COV_frequency_resolution;
            r.severity = QC_SEV_vendor_repairable;
            r.has_repair = true;
            r.repair = QC_REPAIR_quantize_frequency;
            r.element = element;
            r.quantity = QC_QTY_frequency;
            r.value = freq;
            r.limit = c->resolution;
            if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
        }
    }
    return true;
}

static bool check_phase(Check *k, uint32_t frame, Rat phase, int64_t element) {
    const CapChannel *dc = desc_channel_of_frame(k, frame);
    const Constraint *c = find_constraint(dc, QC_RULE_phase_resolution);
    int64_t q;
    bool exact;

    if (c == NULL || !c->has_resolution) return true;
    cover(k, QC_COV_phase_resolution, QC_CSTAT_checked);
    if (!rat_exact_div(phase, c->resolution, &q, &exact))
        return tool_error(k, "arithmetic overflow (phase resolution)");
    if (!exact) {
        Rejection r;
        r.rule = QC_COV_phase_resolution;
        r.severity = c->severity;
        r.has_repair = true;
        r.repair = QC_REPAIR_quantize_phase;
        r.element = element;
        r.quantity = QC_QTY_phase;
        r.value = phase;
        r.limit = c->resolution;
        if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
    }
    return true;
}

static bool check_waveform(Check *k, uint32_t frame, uint32_t wf, int64_t element,
                           bool *env_seen, int64_t *env_used, size_t n_wf) {
    uint32_t pci = k->prog->frames[frame].channel;
    const CapChannel *dc = &k->desc->channels[k->bind[pci]];
    const IrWaveform *w = &k->prog->waveforms[wf];

    if (w->kind == WF_CONST) {
        const Constraint *c = find_constraint(dc, QC_RULE_amplitude_range);
        bool below, above;
        if (c == NULL) return true;
        cover(k, QC_COV_amplitude_range, QC_CSTAT_checked);
        below = c->has_min && rat_cmp(w->as.constant.amplitude, c->min) < 0;
        above = c->has_max && rat_cmp(w->as.constant.amplitude, c->max) > 0;
        if (below || above) {
            Rejection r;
            r.rule = QC_COV_amplitude_range;
            r.severity = c->severity;
            r.has_repair = (c->severity == QC_SEV_vendor_repairable);
            r.repair = QC_REPAIR_trunc_gain;
            r.element = element;
            r.quantity = QC_QTY_amplitude;
            r.value = w->as.constant.amplitude;
            r.limit = below ? c->min : c->max;
            if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
        }
        if (c->has_resolution) {
            int64_t q;
            bool exact;
            cover(k, QC_COV_amplitude_resolution, QC_CSTAT_checked);
            if (!rat_exact_div(w->as.constant.amplitude, c->resolution, &q, &exact))
                return tool_error(k, "arithmetic overflow (amplitude resolution)");
            if (!exact) {
                Rejection r;
                r.rule = QC_COV_amplitude_resolution;
                r.severity = QC_SEV_vendor_repairable;
                r.has_repair = true;
                r.repair = QC_REPAIR_trunc_gain;
                r.element = element;
                r.quantity = QC_QTY_amplitude;
                r.value = w->as.constant.amplitude;
                r.limit = c->resolution;
                if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
            }
        }
        return true;
    }

    /* samples */
    {
        int64_t n = (int64_t)w->as.samples.len;

        if (dc->capabilities.has_envelope_sample_grid) {
            int64_t grid = dc->capabilities.envelope_sample_grid;
            cover(k, QC_COV_envelope_sample_grid, QC_CSTAT_checked);
            if (floor_mod(n, grid) != 0) {
                const Constraint *c = find_constraint(dc, QC_RULE_envelope_sample_grid);
                Rejection r;
                r.rule = QC_COV_envelope_sample_grid;
                r.severity = c != NULL ? c->severity : QC_SEV_fatal;
                r.has_repair = false;
                r.repair = QC_REPAIR_quantize_duration;
                r.element = element;
                r.quantity = QC_QTY_count;
                r.value = rat_from_int(n);
                r.limit = rat_from_int(grid);
                if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
            }
        }

        if (dc->capabilities.has_envelope_max_abs) {
            int64_t max_abs = dc->capabilities.envelope_max_abs;
            int64_t peak = 0;
            size_t i;
            cover(k, QC_COV_envelope_amplitude, QC_CSTAT_checked);
            /* |sample| exceeds i64 only at INT64_MIN, where the magnitude is
             * 2^63. That is not reportable as a Rat numerator, so it is a
             * tool error rather than a verdict computed from a clamped
             * value. */
            for (i = 0; i < w->as.samples.len; i++) {
                uint64_t m = abs_i64(w->as.samples.i[i]);
                if (m > (uint64_t)INT64_MAX)
                    return tool_error(k, "arithmetic overflow (envelope sample magnitude)");
                if ((int64_t)m > peak) peak = (int64_t)m;
            }
            for (i = 0; i < w->as.samples.len; i++) {
                uint64_t m = abs_i64(w->as.samples.q[i]);
                if (m > (uint64_t)INT64_MAX)
                    return tool_error(k, "arithmetic overflow (envelope sample magnitude)");
                if ((int64_t)m > peak) peak = (int64_t)m;
            }
            if (peak > max_abs) {
                const Constraint *c = find_constraint(dc, QC_RULE_envelope_amplitude);
                Rejection r;
                r.rule = QC_COV_envelope_amplitude;
                r.severity = c != NULL ? c->severity : QC_SEV_fatal;
                r.has_repair = false;
                r.repair = QC_REPAIR_trunc_gain;
                r.element = element;
                r.quantity = QC_QTY_amplitude;
                r.value.num = peak;
                r.value.den = 1;
                r.limit = rat_from_int(max_abs);
                if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
            }
        }

        if (dc->capabilities.has_envelope_memory_samples) {
            int64_t mem = dc->capabilities.envelope_memory_samples;
            size_t slot = (size_t)pci * n_wf + wf;
            cover(k, QC_COV_envelope_memory, QC_CSTAT_checked);
            if (!env_seen[slot]) {
                int64_t before = env_used[pci];
                env_seen[slot] = true;
                if (add_overflow_i64(before, n, &env_used[pci]))
                    return tool_error(k, "arithmetic overflow (envelope memory)");
                if (before <= mem && env_used[pci] > mem) {
                    Rejection r;
                    r.rule = QC_COV_envelope_memory;
                    r.severity = QC_SEV_fatal;
                    r.has_repair = false;
                    r.repair = QC_REPAIR_trunc_gain;
                    r.element = element;
                    r.quantity = QC_QTY_count;
                    r.value = rat_from_int(env_used[pci]);
                    r.limit = rat_from_int(mem);
                    if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
                }
            }
        } else {
            cover(k, QC_COV_envelope_memory, QC_CSTAT_unchecked);
        }
    }
    return true;
}

/* Convert a program-unit duration to descriptor units, checking sign,
 * exactness, grid and range; advance the frame clock. */
static bool advance(Check *k, FrameState *states, uint32_t frame, int64_t duration,
                    int64_t element, bool timed_output, int64_t *out_units) {
    uint32_t pci = k->prog->frames[frame].channel;
    const CapChannel *dc = &k->desc->channels[k->bind[pci]];
    FrameState *st = &states[frame];
    Rat dur_seconds, grid_limit;
    const Constraint *grid_c;
    CoverageClass rule;
    RepairId repair;
    Severity severity;
    int64_t dur_units = 0;
    bool off_grid = false;
    int64_t exact_units;
    bool exact;

    if (!rat_mul_int(k->prog->channels[pci].unit, duration, &dur_seconds))
        return tool_error(k, "arithmetic overflow (duration)");

    if (duration < 0) {
        Rejection r;
        r.rule = QC_COV_negative_duration;
        r.severity = QC_SEV_fatal;
        r.has_repair = false;
        r.repair = QC_REPAIR_quantize_duration;
        r.element = element;
        r.quantity = QC_QTY_time;
        r.value = dur_seconds;
        r.limit = RAT_ZERO;
        if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
        cover(k, QC_COV_negative_duration, QC_CSTAT_checked);
        *out_units = 0; /* clock unchanged; nothing sane to advance by */
        return true;
    }
    cover(k, QC_COV_negative_duration, QC_CSTAT_checked);

    /* Output durations (play/capture) live on the channel's duration grid.
     * Delay durations carry no grid of their own — a gap from an off-grid
     * pulse end to an on-grid start is legitimate — so output start times are
     * checked against the schedule grid at each play/capture instead. A
     * duration off the unit lattice entirely is always a rejection. */
    grid_c = find_constraint(dc, QC_RULE_pulse_length_grid);
    rule = timed_output ? QC_COV_pulse_length_grid : QC_COV_schedule_grid;
    repair = timed_output ? QC_REPAIR_quantize_duration : QC_REPAIR_quantize_time;
    if (timed_output)
        severity = grid_c != NULL ? grid_c->severity : QC_SEV_vendor_repairable;
    else
        severity = QC_SEV_vendor_repairable;
    if (!rat_mul_int(dc->unit, timed_output ? dc->duration_grid : dc->schedule_grid, &grid_limit))
        return tool_error(k, "arithmetic overflow (grid limit)");

    if (!rat_exact_mul_int(k->factor[pci], duration, &exact_units, &exact))
        return tool_error(k, "arithmetic overflow (unit conversion)");
    if (exact) {
        dur_units = exact_units;
        off_grid = timed_output && floor_mod(dur_units, dc->duration_grid) != 0;
    } else {
        /* does not land on the descriptor unit lattice at all */
        Rat q;
        off_grid = true;
        if (!rat_mul_int(k->factor[pci], duration, &q))
            return tool_error(k, "arithmetic overflow (unit conversion)");
        dur_units = rat_round_nearest_even(q);
    }
    if (off_grid) {
        Rejection r;
        r.rule = rule;
        r.severity = severity;
        r.has_repair = true;
        r.repair = repair;
        r.element = element;
        r.quantity = QC_QTY_time;
        r.value = dur_seconds;
        r.limit = grid_limit;
        if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
    }
    if (timed_output && grid_c != NULL) cover(k, QC_COV_pulse_length_grid, QC_CSTAT_checked);

    if (timed_output) {
        RuleId range_id = dc->kind == QC_CHKIND_drive ? QC_RULE_pulse_length_range
                                                      : QC_RULE_readout_length_range;
        const Constraint *c = find_constraint(dc, range_id);
        if (c != NULL) {
            bool below = c->has_min_units && dur_units < c->min_units;
            bool above = c->has_max_units && dur_units > c->max_units;
            cover(k, rule_class(range_id), QC_CSTAT_checked);
            if (below || above) {
                int64_t bound = below ? c->min_units : c->max_units;
                Rejection r;
                r.rule = rule_class(range_id);
                r.severity = c->severity;
                r.has_repair = false;
                r.repair = QC_REPAIR_quantize_duration;
                r.element = element;
                r.quantity = QC_QTY_time;
                r.value = dur_seconds;
                if (!rat_mul_int(dc->unit, bound, &r.limit))
                    return tool_error(k, "arithmetic overflow (range limit)");
                if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
            }
        }
    }

    if (add_overflow_i64(st->clock, dur_units, &st->clock))
        return tool_error(k, "arithmetic overflow (frame clock)");
    *out_units = dur_units;
    return true;
}

/* per_item * count + overhead, refusing to wrap. The coefficients come from
 * the descriptor, so the product is as untrusted as any other input: signed
 * overflow here would be undefined behaviour, and a wrapped total would be a
 * budget verdict computed from a number the device never had. */
static bool budget_cost(int64_t per_item, int64_t count, int64_t overhead, int64_t *out) {
    int64_t product;
    if (__builtin_mul_overflow(per_item, count, &product)) return false;
    return !add_overflow_i64(product, overhead, out);
}

/* Deterministic order: rules in registry order, then file order. The
 * comparator is a total order, so qsort's instability is irrelevant. */
static int compare_entries(const void *a, const void *b) {
    const SinkEntry *x = a, *y = b;
    if (x->rej.rule != y->rej.rule) return x->rej.rule < y->rej.rule ? -1 : 1;
    if (x->seq != y->seq) return x->seq < y->seq ? -1 : 1;
    return 0;
}

bool check(Arena *a, const IrProgram *prog, const Descriptor *desc, Report *out, Diag *diag) {
    Check kk;
    Check *k = &kk;
    uint32_t *bind;
    Rat *factor;
    FrameState *states;
    bool *env_seen, *wf_played;
    int64_t *env_used;
    BudgetResult *budgets;
    size_t n_budgets = 0;
    Rejection *rejections;
    size_t i, j;
    int64_t n_plays = 0, n_captures = 0, last_element = 0;
    int64_t wf_count = 0;
    bool any_fatal = false;

    if (!validate_descriptor(desc, diag)) return false;

    memset(k, 0, sizeof *k);
    k->arena = a;
    k->prog = prog;
    k->desc = desc;
    k->diag = diag;
    k->sink.arena = a;
    for (i = 0; i < QC_COVERAGE_CLASS_COUNT; i++) k->coverage[i] = QC_CSTAT_not_applicable;

    /* bind program channels to descriptor channels by name */
    bind = arena_array(a, prog->n_channels, sizeof *bind);
    factor = arena_array(a, prog->n_channels, sizeof *factor);
    if ((bind == NULL || factor == NULL) && prog->n_channels != 0)
        return tool_error(k, "out of memory");
    for (i = 0; i < prog->n_channels; i++) {
        bool found = false;
        for (j = 0; j < desc->n_channels; j++) {
            if (str_eq(prog->channels[i].name, desc->channels[j].name)) {
                bind[i] = (uint32_t)j;
                if (!rat_div(prog->channels[i].unit, desc->channels[j].unit, &factor[i]))
                    return tool_error(k, "channel '" STR_FMT "': unit conversion overflow",
                                      STR_ARG(prog->channels[i].name));
                found = true;
                break;
            }
        }
        if (!found)
            return tool_error(k, "program channel '" STR_FMT "' not in descriptor",
                              STR_ARG(prog->channels[i].name));
    }
    k->bind = bind;
    k->factor = factor;

    /* A bound channel that declares no constraints is declared, not checked. */
    for (i = 0; i < prog->n_channels; i++)
        if (desc->channels[bind[i]].n_constraints == 0)
            cover(k, QC_COV_unconstrained_channel, QC_CSTAT_unchecked);

    states = arena_array(a, prog->n_frames, sizeof *states);
    if (states == NULL && prog->n_frames != 0) return tool_error(k, "out of memory");
    for (i = 0; i < prog->n_frames; i++) {
        states[i].clock = 0;
        states[i].frequency = prog->frames[i].frequency;
        states[i].phase = prog->frames[i].phase;
        states[i].freq_checked = false;
        states[i].phase_checked = false;
    }

    /* Per-(channel, waveform) envelope accounting: a waveform's envelope is
     * loaded once per channel however many plays reference it. */
    env_seen = arena_array(a, prog->n_channels * prog->n_waveforms + 1, sizeof *env_seen);
    env_used = arena_array(a, prog->n_channels + 1, sizeof *env_used);
    wf_played = arena_array(a, prog->n_waveforms + 1, sizeof *wf_played);
    if (env_seen == NULL || env_used == NULL || wf_played == NULL)
        return tool_error(k, "out of memory");

    for (i = 0; i < prog->n_elements; i++) {
        const IrElement *el = &prog->elements[i];
        last_element = el->id;

        switch (el->kind) {
        case EL_BARRIER: {
            const uint32_t *members = el->as.barrier.frames;
            size_t n_members = el->as.barrier.len;
            uint32_t *all = NULL;
            Rat max_s = RAT_ZERO;
            size_t m;

            if (n_members == 0) {
                all = arena_array(a, prog->n_frames, sizeof *all);
                if (all == NULL && prog->n_frames != 0) return tool_error(k, "out of memory");
                for (m = 0; m < prog->n_frames; m++) all[m] = (uint32_t)m;
                members = all;
                n_members = prog->n_frames;
            }
            /* latest frame time in absolute seconds, exact */
            for (m = 0; m < n_members; m++) {
                uint32_t fi = members[m];
                Rat unit = desc_channel_of_frame(k, fi)->unit;
                Rat t_s;
                if (!scale(k, unit, states[fi].clock, &t_s)) return false;
                if (rat_cmp(t_s, max_s) > 0) max_s = t_s;
            }
            for (m = 0; m < n_members; m++) {
                uint32_t fi = members[m];
                const CapChannel *dc = desc_channel_of_frame(k, fi);
                int64_t t_units;
                bool exact;
                if (!rat_exact_div(max_s, dc->unit, &t_units, &exact))
                    return tool_error(k, "arithmetic overflow (barrier alignment)");
                if (exact) {
                    states[fi].clock = t_units;
                } else {
                    Rat q;
                    Rejection r;
                    if (!rat_div(max_s, dc->unit, &q))
                        return tool_error(k, "arithmetic overflow (barrier alignment)");
                    r.rule = QC_COV_schedule_grid;
                    r.severity = QC_SEV_vendor_repairable;
                    r.has_repair = true;
                    r.repair = QC_REPAIR_quantize_time;
                    r.element = el->id;
                    r.quantity = QC_QTY_time;
                    r.value = max_s;
                    if (!scale(k, dc->unit, dc->schedule_grid, &r.limit)) return false;
                    if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
                    cover(k, QC_COV_schedule_grid, QC_CSTAT_checked);
                    states[fi].clock = rat_round_nearest_even(q);
                }
            }
            break;
        }

        case EL_SHIFT_PHASE: {
            FrameState *st = &states[el->as.shift_phase.frame];
            if (!rat_add(st->phase, el->as.shift_phase.phase, &st->phase))
                return tool_error(k, "arithmetic overflow (phase accumulation)");
            if (!check_phase(k, el->as.shift_phase.frame, el->as.shift_phase.phase, el->id))
                return false;
            break;
        }

        case EL_SET_FREQUENCY: {
            FrameState *st = &states[el->as.set_frequency.frame];
            st->frequency = el->as.set_frequency.frequency;
            st->freq_checked = false;
            if (!check_frequency(k, el->as.set_frequency.frame,
                                 el->as.set_frequency.frequency, el->id))
                return false;
            st->freq_checked = true;
            break;
        }

        case EL_DELAY: {
            int64_t units;
            if (!advance(k, states, el->as.delay.frame, el->as.delay.duration, el->id, false, &units))
                return false;
            break;
        }

        case EL_PLAY:
        case EL_CAPTURE: {
            bool is_play = (el->kind == EL_PLAY);
            uint32_t frame = is_play ? el->as.play.frame : el->as.capture.frame;
            int64_t dur = is_play ? el->as.play.duration : el->as.capture.duration;
            FrameState *st = &states[frame];
            const CapChannel *dc = desc_channel_of_frame(k, frame);
            int64_t units;

            if (is_play)
                n_plays++;
            else
                n_captures++;

            /* lazy initial frame-state checks, attributed to first use */
            if (!st->freq_checked) {
                if (!check_frequency(k, frame, st->frequency, el->id)) return false;
                st->freq_checked = true;
            }
            if (!st->phase_checked) {
                if (!check_phase(k, frame, st->phase, el->id)) return false;
                st->phase_checked = true;
            }

            /* schedule grid on the start time */
            if (floor_mod(st->clock, dc->schedule_grid) != 0) {
                Rejection r;
                r.rule = QC_COV_schedule_grid;
                r.severity = QC_SEV_vendor_repairable;
                r.has_repair = true;
                r.repair = QC_REPAIR_quantize_time;
                r.element = el->id;
                r.quantity = QC_QTY_time;
                if (!scale(k, dc->unit, st->clock, &r.value)) return false;
                if (!scale(k, dc->unit, dc->schedule_grid, &r.limit)) return false;
                if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
            }
            cover(k, QC_COV_schedule_grid, QC_CSTAT_checked);

            if (!advance(k, states, frame, dur, el->id, true, &units)) return false;

            if (is_play) {
                uint32_t wf = el->as.play.waveform;
                wf_played[wf] = true;
                if (!check_waveform(k, frame, wf, el->id, env_seen, env_used, prog->n_waveforms))
                    return false;
            }
            break;
        }
        }
    }

    /* budgets */
    budgets = arena_array(a, desc->n_budgets + 1, sizeof *budgets);
    if (budgets == NULL) return tool_error(k, "out of memory");
    for (i = 0; i < prog->n_waveforms; i++)
        if (wf_played[i]) wf_count++;

    for (i = 0; i < desc->n_budgets; i++) {
        const Budget *b = &desc->budgets[i];
        int64_t lower, upper;
        BudgetVerdict verdict;
        CoverageClass cls;

        switch (b->id) {
        case QC_BUDGET_wmem_words:
            if (!budget_cost(b->cost.per_item, wf_count, b->cost.overhead, &lower))
                return tool_error(k, "arithmetic overflow (budget cost model)");
            upper = lower;
            cls = QC_COV_wmem_words;
            break;
        case QC_BUDGET_pmem_words: {
            /* calibrated: one word per output element plus fixed overhead;
             * non-output elements bounded at two words each (survey asm) */
            int64_t doubled;
            if (!budget_cost(b->cost.per_item, n_plays + n_captures, b->cost.overhead, &lower))
                return tool_error(k, "arithmetic overflow (budget cost model)");
            if (__builtin_mul_overflow((int64_t)prog->n_elements, (int64_t)2, &doubled))
                return tool_error(k, "arithmetic overflow (budget cost model)");
            if (!budget_cost(b->cost.per_item, doubled, b->cost.overhead, &upper))
                return tool_error(k, "arithmetic overflow (budget cost model)");
            cls = QC_COV_pmem_words;
            break;
        }
        case QC_BUDGET_loop_registers:
        default:
            cover(k, QC_COV_loop_registers, QC_CSTAT_not_applicable);
            continue; /* the program format has no loops */
        }

        if (upper <= b->limit)
            verdict = QC_BVERDICT_fits;
        else if (lower > b->limit)
            verdict = QC_BVERDICT_violates;
        else
            verdict = QC_BVERDICT_indeterminate;

        cover(k, cls,
              verdict == QC_BVERDICT_indeterminate ? QC_CSTAT_indeterminate : QC_CSTAT_checked);

        if (verdict == QC_BVERDICT_violates) {
            Rejection r;
            r.rule = cls;
            r.severity = QC_SEV_fatal;
            r.has_repair = false;
            r.repair = QC_REPAIR_trunc_gain;
            r.element = last_element;
            r.quantity = QC_QTY_count;
            r.value = rat_from_int(lower);
            r.limit = rat_from_int(b->limit);
            if (!sink_add(&k->sink, r)) return tool_error(k, "out of memory");
        }

        budgets[n_budgets].id = b->id;
        budgets[n_budgets].verdict = verdict;
        budgets[n_budgets].lower = lower;
        budgets[n_budgets].upper = upper;
        budgets[n_budgets].limit = b->limit;
        n_budgets++;
    }

    if (k->sink.oom) return tool_error(k, "out of memory");

    /* qsort's first argument is declared non-null even for a count of zero,
     * and the sink is never allocated when nothing was rejected. */
    if (k->sink.len > 1)
        qsort(k->sink.items, k->sink.len, sizeof *k->sink.items, compare_entries);
    rejections = arena_array(a, k->sink.len + 1, sizeof *rejections);
    if (rejections == NULL) return tool_error(k, "out of memory");
    for (i = 0; i < k->sink.len; i++) {
        rejections[i] = k->sink.items[i].rej;
        if (rejections[i].severity == QC_SEV_fatal) any_fatal = true;
    }

    out->ident = desc->ident;
    out->verdict = any_fatal ? QC_VERDICT_fail
                             : (k->sink.len > 0 ? QC_VERDICT_pass_with_repairs : QC_VERDICT_pass);
    out->rejections = rejections;
    out->n_rejections = k->sink.len;
    out->budgets = budgets;
    out->n_budgets = n_budgets;
    memcpy(out->coverage, k->coverage, sizeof out->coverage);
    return true;
}
