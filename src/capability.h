#ifndef QCONFORM_CAPABILITY_H
#define QCONFORM_CAPABILITY_H

/* The device capability descriptor, pure data. Evidence and vendor_behavior
 * entries are authoring-side: parse.c type-checks their containers and drops
 * the contents.
 *
 * Optional fields carry an explicit has_ flag rather than a sentinel. This is
 * a checker whose entire job is bounds, and a sentinel is how -1 quietly
 * starts meaning "absent" for a field where -1 is a legal value.
 */

#include <stdbool.h>
#include <stdint.h>

#include "enums.h"
#include "str.h"
#include "rational.h"

typedef struct {
    Str name;
    Str board;
    Str fw_timestamp;
    Str cfg_sw_version;
    Str library_name;
    Str library_version;
    Str descriptor_version;
} Identification;

typedef struct {
    RoundMode time;
    RoundMode frequency;
    RoundMode phase;
    RoundMode amplitude;
} Rounding;

typedef struct {
    RuleId id;
    Quantity quantity;
    Shape shape;
    Severity severity;

    int64_t min_units;
    bool has_min_units;
    int64_t max_units;
    bool has_max_units;

    Rat min;
    bool has_min;
    Rat max;
    bool has_max;
    Rat resolution;
    bool has_resolution;

    bool post_mixer;

    /* amplitude_range and envelope_amplitude: a value above saturate_max and
     * not above max is accepted and clamped to saturate_max, a repair. Above
     * max it is refused. Qblox gain is a signed 16-bit register, so +1.0
     * lands on 32767/32768 while -1.0 is exact. */
    Rat saturate_max;
    bool has_saturate_max;

    /* start_spacing: a frame whose initial phase is not zero starts with a
     * phase update at time 0, which counts as an operation. On Qblox the
     * carrier has no initial phase, so the phase is set by an update. */
    bool initial_phase_update;

    int64_t grid;
    bool has_grid;
} Constraint;

typedef struct {
    bool phrst;
    bool has_phrst;
    int64_t n_tones;
    bool has_n_tones;
    int64_t envelope_memory_samples;
    bool has_envelope_memory_samples;
    int64_t envelope_sample_grid;
    bool has_envelope_sample_grid;
    int64_t envelope_max_abs;
    bool has_envelope_max_abs;
    /* The envelope memory holds envelope_memory_samples per frame rather
     * than per channel. Qblox waveform memory belongs to a sequencer, and a
     * sequencer serves one channel and one carrier. */
    bool envelope_memory_per_frame;
} Capabilities;

typedef struct {
    Str name;
    ChannelKind kind;
    Str vendor_type;
    Rat unit;
    Rat sample_unit;
    bool has_sample_unit;
    int64_t duration_grid;
    int64_t schedule_grid;
    Capabilities capabilities;
    const Constraint *constraints;
    size_t n_constraints;
} CapChannel;

typedef struct {
    CostKind kind;
    int64_t per_item;  /* defaults to 1 */
    int64_t overhead;  /* defaults to 0 */
    int64_t reserved_min;
    bool has_reserved_min;
    int64_t reserved_max;
    bool has_reserved_max;
    /* The most words one element can cost, for the upper bound. Absent, the
     * bound is two words per element at per_item each, the QICK survey
     * calibration. */
    int64_t per_element_max;
    bool has_per_element_max;
} CostModel;

typedef struct {
    BudgetId id;
    int64_t limit;
    CostModel cost;
    /* Counted per frame instead of for the whole program, over the frames of
     * the named channels only (every channel when n_channels is 0). A Qblox
     * sequencer has its own instruction memory, and the size differs by
     * module type. */
    bool per_frame;
    const Str *channels;
    size_t n_channels;
} Budget;

typedef struct {
    Identification ident;
    FramesMode frames;
    Rounding rounding;
    const CapChannel *channels;
    size_t n_channels;
    const Budget *budgets;
    size_t n_budgets;
} Descriptor;

#endif /* QCONFORM_CAPABILITY_H */
