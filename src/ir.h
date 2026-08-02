#ifndef QCONFORM_IR_H
#define QCONFORM_IR_H

/* The pulse program, pure data: the JSON program format parsed into
 * index-resolved form. Names are bound to indices by parse.c and kept only so
 * diagnostics can quote them. */

#include <stdint.h>

#include "enums.h"
#include "str.h"
#include "rational.h"

typedef struct {
    Str name;
    Rat unit;             /* seconds per time unit, strictly positive */
    Rat sample_unit;      /* valid only when has_sample_unit */
    bool has_sample_unit;
    /* Digital mixer frequency in Hz, signed. Present only when the channel
     * is configured with one. A constraint whose post_mixer is set is
     * checked against the frequency after this is subtracted, because that
     * is what the device sees. */
    Rat mixer_frequency;
    bool has_mixer_frequency;
} IrChannel;

typedef struct {
    Str name;
    uint32_t channel;
    Rat frequency;
    Rat phase;
} IrFrame;

typedef enum { WF_CONST, WF_SAMPLES } WaveformKind;

typedef struct {
    /* name is common to both variants, so it lives outside the union */
    Str name;
    WaveformKind kind;
    union {
        struct {
            Rat amplitude;
        } constant;
        struct {
            int64_t full_scale;
            const int64_t *i;
            const int64_t *q;
            size_t len; /* i and q share a length */
        } samples;
    } as;
} IrWaveform;

typedef enum {
    EL_PLAY,
    EL_CAPTURE,
    EL_DELAY,
    EL_BARRIER,
    EL_SHIFT_PHASE,
    EL_SET_FREQUENCY
} ElementKind;

typedef struct {
    /* id is common to every variant, so it lives outside the union */
    int64_t id;
    ElementKind kind;
    union {
        struct {
            uint32_t frame;
            uint32_t waveform;
            int64_t duration;
        } play;
        struct {
            uint32_t frame;
            int64_t duration;
        } capture;
        struct {
            uint32_t frame;
            int64_t duration;
        } delay;
        struct {
            const uint32_t *frames;
            size_t len; /* zero means every frame in the file */
        } barrier;
        struct {
            uint32_t frame;
            Rat phase;
        } shift_phase;
        struct {
            uint32_t frame;
            Rat frequency;
        } set_frequency;
    } as;
} IrElement;

typedef struct {
    const IrChannel *channels;
    size_t n_channels;
    const IrFrame *frames;
    size_t n_frames;
    const IrWaveform *waveforms;
    size_t n_waveforms;
    const IrElement *elements;
    size_t n_elements;
} IrProgram;

#endif /* QCONFORM_IR_H */
