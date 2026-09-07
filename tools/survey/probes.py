"""Probe definitions for the asm_v2 constraint survey.

Every probe list is derived deterministically from config values (no RNG,
no clocks). A probe is a dict: axis, kind, target channel, parameter
overrides, and a note naming the constraint candidate it exercises.
"""


def gen_classes(soccfg):
    """Unique generator behavior classes, one representative channel each,
    plus one duplicate channel per class (if any) for config-dependence
    cross-checks."""
    classes = {}
    for ch, g in enumerate(soccfg['gens']):
        key = (g['type'], g.get('maxlen'), g['f_fabric'], g['f_dds'],
               g['interpolation'], g['maxv'])
        classes.setdefault(key, []).append(ch)
    out = []
    for key, chs in classes.items():
        out.append({'ch': chs[0], 'dup_ch': chs[1] if len(chs) > 1 else None,
                    'gcfg': soccfg['gens'][chs[0]]})
    return out


def length_values(f_fabric, maxlen_cycles=2**16):
    """Pulse lengths in us hitting boundaries and rounding ties."""
    cyc = lambda c: c / f_fabric
    vals = []
    for c, note in [
            (-599, 'negative length'),
            (0, 'zero length'),
            (1, 'below min 3 cycles'),
            (2, 'below min 3 cycles'),
            (3, 'min boundary'),
            (4, 'just above min'),
            (60, 'nominal'),
            (maxlen_cycles - 1, 'max boundary'),
            (maxlen_cycles, 'first over max'),
            (maxlen_cycles + 1, 'over max')]:
        vals.append((cyc(c), note))
    # rounding ties: n+0.5 cycles; half-even rounds 10.5->10, 11.5->12
    for n in (10, 11, 12, 13):
        vals.append((cyc(n + 0.5), f'tie {n}+0.5 cycles'))
    for n, frac in ((20, 0.25), (20, 0.75), (21, 0.25), (21, 0.75)):
        vals.append((cyc(n + frac), f'fraction {n}+{frac} cycles'))
    vals.append((0.1003, 'phase-0 reference case'))
    return vals


def freq_values(gcfg):
    """Frequencies in MHz probing the DDS range and quantization."""
    f_dds = gcfg['f_dds']
    fstep = f_dds / 2**gcfg['b_dds']
    vals = [
        (0.0, 'zero'),
        (f_dds / 4, 'mid band'),
        (f_dds / 2 - 1.0, 'near upper edge'),
        (f_dds / 2, 'upper edge'),
        (f_dds / 2 + 1.0, 'just over upper edge'),
        (-f_dds / 2, 'lower edge'),
        (-f_dds / 2 - 1.0, 'just under lower edge'),
        (f_dds, 'full f_dds'),
        (1.5 * f_dds, '1.5x f_dds'),
        (100.0 + 0.3 * fstep, 'quantization +0.3 step'),
        (100.0 + 0.5 * fstep, 'quantization +0.5 step'),
        (100.0 + 0.7 * fstep, 'quantization +0.7 step'),
    ]
    return vals


def gain_values(maxv):
    """Gains (relative full-scale) probing bounds and truncation mode."""
    return [
        (0.5, 'nominal'),
        (1.0, 'full scale'),
        (-1.0, 'negative full scale'),
        (1.001, 'just over full scale'),
        (1.5, 'well over full scale'),
        (-1.5, 'well under negative full scale'),
        (0.0, 'zero'),
        # truncation probes: request maxv*g with fractional raw values
        ((int(0.5 * maxv) + 0.4) / maxv, 'raw +0.4 (trunc vs round)'),
        ((int(0.5 * maxv) + 0.6) / maxv, 'raw +0.6 (trunc vs round)'),
        (-(int(0.5 * maxv) + 0.6) / maxv, 'raw -0.6 (trunc toward zero)'),
    ]


def phase_values(b_phase):
    """Phases in degrees probing wrap and resolution rounding."""
    pstep = 360.0 / 2**b_phase
    return [
        (0.0, 'zero'),
        (90.0, 'nominal'),
        (359.999, 'near full turn'),
        (360.0, 'full turn (wrap)'),
        (720.0, 'two turns (wrap)'),
        (-90.0, 'negative'),
        (45.0 + 0.3 * pstep, 'quantization +0.3 step'),
        (45.0 + 0.5 * pstep, 'quantization +0.5 step'),
        (45.0 + 0.7 * pstep, 'quantization +0.7 step'),
    ]


def build_probes(soccfg, synthetic_variants=True):
    probes = []
    tproc = soccfg['tprocs'][0]

    for cls in gen_classes(soccfg):
        ch, gcfg = cls['ch'], cls['gcfg']
        gt = gcfg['type']
        mux = 'mux' in gt
        base = {'gen_ch': ch, 'gen_type': gt, 'dup_ch': cls['dup_ch']}

        # The tProc time immediate is a signed 32-bit field, so the value
        # that must fit is 2**31 - 1 and not the 2**32 the mux pulse-length
        # check itself allows. Probing to 2**32 recorded a max boundary that
        # rejects, which is how the descriptor came to declare a maximum the
        # toolchain refuses.
        maxcyc = 2**31 if mux else 2**16
        for v, note in length_values(gcfg['f_fabric'], maxcyc):
            style = 'mux_const' if mux else 'const'
            probes.append({**base, 'axis': 'length', 'kind': style,
                           'param': 'length', 'requested': v, 'note': note})
        if cls['dup_ch'] is not None and not mux:
            probes.append({**base, 'gen_ch': cls['dup_ch'], 'axis': 'length',
                           'kind': 'const', 'param': 'length',
                           'requested': 60.5 / gcfg['f_fabric'],
                           'note': 'duplicate-channel cross-check (same class)'})

        if not mux:
            for v, note in freq_values(gcfg):
                probes.append({**base, 'axis': 'freq', 'kind': 'const',
                               'param': 'freq', 'requested': v, 'note': note})
            for v, note in gain_values(gcfg['maxv']):
                probes.append({**base, 'axis': 'gain', 'kind': 'const',
                               'param': 'gain', 'requested': v, 'note': note})
            for v, note in phase_values(gcfg['b_phase']):
                probes.append({**base, 'axis': 'phase', 'kind': 'const',
                               'param': 'phase', 'requested': v, 'note': note})
            probes.append({**base, 'axis': 'phrst', 'kind': 'const',
                           'param': 'phrst', 'requested': 1,
                           'note': 'phrst support gated by gen type'})

        # envelope axes only where an envelope memory exists
        if 'maxlen' in gcfg and not mux:
            spc = gcfg['samps_per_clk']
            maxlen = gcfg['maxlen']
            for nsamp, note in [
                    (16 * spc, 'aligned envelope'),
                    (16 * spc + 1, 'length not multiple of samps_per_clk'),
                    (16 * spc - 1, 'length not multiple of samps_per_clk'),
                    (maxlen, 'envelope exactly fills memory'),
                    (maxlen + spc, 'envelope exceeds memory (suspected unchecked)')]:
                probes.append({**base, 'axis': 'envelope', 'kind': 'arb',
                               'param': 'env_samples', 'requested': nsamp,
                               'note': note})
            probes.append({**base, 'axis': 'envelope', 'kind': 'arb',
                           'param': 'env_maxv', 'requested': gcfg['maxv'] + 1,
                           'env_samples': 16 * spc,
                           'note': 'envelope amplitude over maxv'})
            probes.append({**base, 'axis': 'envelope', 'kind': 'env_two',
                           'param': 'env_samples',
                           'requested': (maxlen // 2 // spc * spc) + (maxlen // 2 // spc * spc),
                           'note': 'two envelopes jointly near memory size'})

        if mux:
            probes.append({**base, 'axis': 'mux', 'kind': 'mux_const',
                           'param': 'mask', 'requested': 8,
                           'note': 'mask tone index 8 with 2 tones declared '
                                   '(out of range)'})
            probes.append({**base, 'axis': 'phrst', 'kind': 'mux_const',
                           'param': 'phrst', 'requested': 1,
                           'note': 'phrst on mux gen (expect unsupported)'})

    # whole-program budgets (config-level, use first standard gen)
    std_ch = next(c['ch'] for c in gen_classes(soccfg) if 'mux' not in c['gcfg']['type'])
    for n in (8, tproc['wmem_size'] - 8, tproc['wmem_size'] + 8):
        probes.append({'axis': 'budget_wmem', 'kind': 'many_waveforms',
                       'gen_ch': std_ch, 'param': 'n_waveforms', 'requested': n,
                       'note': f'waveform memory {tproc["wmem_size"]} words'})
    for n in (100, 2000, 8000, 20000):
        probes.append({'axis': 'budget_pmem', 'kind': 'many_instructions',
                       'gen_ch': std_ch, 'param': 'n_pulses', 'requested': n,
                       'note': f'program memory {tproc["pmem_size"]} words'})
    for n in (2, 8, 12, 16, 24):
        probes.append({'axis': 'budget_regs', 'kind': 'many_loops',
                       'gen_ch': std_ch, 'param': 'n_loops', 'requested': n,
                       'note': f'data registers dreg_qty {tproc["dreg_qty"]}'})

    # readout config lengths on the first dynamic readout, if any
    dyn_ro = next((i for i, r in enumerate(soccfg['readouts'])
                   if 'tproc_ctrl' in r), None)
    if dyn_ro is not None:
        rocfg = soccfg['readouts'][dyn_ro]
        f_out = rocfg['f_output']
        for c, note in [(2, 'below min 3'), (3, 'min boundary'),
                        (2**16 - 1, 'max boundary'), (2**16, 'first over max')]:
            probes.append({'axis': 'readout', 'kind': 'ro_config',
                           'ro_ch': dyn_ro, 'gen_ch': std_ch,
                           'param': 'length', 'requested': c / f_out,
                           'note': note})

    # timing
    for t, note in [(-0.1, 'negative pulse time'),
                    (10.5 / soccfg['gens'][std_ch]['f_fabric'], 'tie 10.5 cycles'),
                    (1e7, 'very large pulse time'),
                    (2**31 / (tproc['f_time']), 'wait over 31-bit cycles')]:
        kind = 'wait_long' if note.startswith('wait') else 'pulse_at_t'
        probes.append({'axis': 'timing', 'kind': kind, 'gen_ch': std_ch,
                       'param': 't', 'requested': t, 'note': note})

    # out-of-range channel index
    probes.append({'axis': 'channels', 'kind': 'const', 'gen_ch': len(soccfg['gens']),
                   'gen_type': 'out_of_range', 'param': 'length',
                   'requested': 0.1, 'note': 'generator index out of range'})

    # synthetic config variant: unsupported tproc revision
    if synthetic_variants:
        probes.append({'axis': 'config', 'kind': 'bad_revision', 'gen_ch': std_ch,
                       'param': 'revision', 'requested': 20,
                       'note': 'tproc revision outside ASM_REVISIONS (synthetic config)'})

    probes.extend(gain_lsb_probes(soccfg))
    probes.extend(mux_tone_probes(soccfg))

    return probes


def gain_lsb_probes(soccfg):
    """Find the smallest gain step that moves the raw register.

    The gain axis probes round-trip error at a few notable values. It cannot
    show the granularity, because none of its values sits one step from its
    neighbour. These walk k/maxv for small k, so the register either advances
    by one each time or does not.

    A v6 advances every step. An interpolated generator reports maxv_scale
    below 1 and skips, which is the whole point of the probe.
    """
    out = []
    for cls in gen_classes(soccfg):
        ch, g = cls["ch"], cls["gcfg"]
        for k in range(0, 9):
            out.append({
                "axis": "gain_lsb", "kind": "const", "gen_ch": ch,
                "gen_type": g["type"], "param": "gain",
                "requested": k / g["maxv"],
                "note": f"k={k} of maxv, maxv_scale={g.get('maxv_scale', 1.0)}",
            })
    return out


def tone_table(g, n, freq=None, gains=True, phases=True, drop_gain=0):
    """A tone table of n tones for a muxed generator.

    Tone 0 carries the probed value when freq is given; the rest are spread
    across the band so the table is a table and not one tone repeated. Values
    come from config fields only, as everything in this file does.
    """
    mixer = g['f_dds'] / 4
    freqs = [mixer + g['f_dds'] * (i + 1) / (4 * (n + 1)) for i in range(n)]
    if freq is not None:
        freqs[0] = freq
    t = {'freqs': freqs}
    t['gains'] = [0.5] * (n - drop_gain) if gains else None
    t['phases'] = [0.0] * n if phases else None
    return t


def mux_tone_probes(soccfg):
    """Probe the tone table of a muxed generator.

    The mux axis had one row. It exercised a mask index against the two-tone
    table declare_kwargs happens to build, and nothing else: the freq, gain and
    phase axes are all gated on `not mux`, so a muxed generator's tone values
    were unprobed. A mux pulse carries only style, mask and length, so the
    frequency, gain and phase of a mux channel live in the tone table and have
    to be probed there.

    The band is stated in absolute terms. ABSOLUTE_FREQS is true on tProc v2,
    so a declared tone frequency is absolute and the vendor subtracts the mixer
    itself, which puts the reachable band at mixer +/- f_dds/2.
    """
    out = []
    for cls in gen_classes(soccfg):
        ch, g = cls['ch'], cls['gcfg']
        if 'mux' not in g['type']:
            continue
        base = {'axis': 'mux', 'kind': 'mux_tones', 'gen_ch': ch,
                'gen_type': g['type'], 'tone_index': 0}
        n = g['n_tones']
        mixer = g['f_dds'] / 4
        fstep = g['f_dds'] / 2**g['b_dds']
        half = g['f_dds'] / 2

        # tone frequency, absolute, against a band of mixer +/- f_dds/2
        for v, note in [
                (mixer, 'tone at band centre'),
                (mixer + half / 2, 'tone mid band'),
                (mixer + half / 2 + 0.3 * fstep, 'tone quantization +0.3 step'),
                (mixer - half, 'tone at lower band edge'),
                (mixer - half - fstep, 'tone just under lower edge'),
                (mixer + half, 'tone at upper band edge'),
                (mixer + half + fstep, 'tone just over upper edge'),
                (mixer + 1.5 * g['f_dds'], 'tone at 1.5x f_dds'),
                (mixer - half - g['f_dds'] / 8, 'tone well under lower edge'),
                (mixer - 1.5 * g['f_dds'], 'tone at -1.5x f_dds')]:
            out.append({**base, 'param': 'mux_freq', 'requested': v,
                        'tones': tone_table(g, 2, freq=v), 'note': note})

        # tone gain. The vendor rounds the mux register and applies no
        # maxv_scale, where the non-mux path truncates and applies one, so the
        # +0.4 and +0.6 rungs are what tell the two apart.
        if g.get('has_gain'):
            maxv = g['maxv']
            for k in range(0, 5):
                out.append({**base, 'param': 'mux_gain', 'requested': k / maxv,
                            'tones': _gain_table(g, k / maxv),
                            'note': f'tone k={k} of maxv (mux gain lsb)'})
            for frac, note in [(0.4, 'tone raw +0.4 (round down)'),
                               (0.6, 'tone raw +0.6 (trunc vs round)')]:
                v = (4 + frac) / maxv
                out.append({**base, 'param': 'mux_gain', 'requested': v,
                            'tones': _gain_table(g, v), 'note': note})
            for v, note in [(1.0, 'tone gain at full scale'),
                            (1.0 + 4.0 / maxv, 'tone gain over full scale')]:
                out.append({**base, 'param': 'mux_gain', 'requested': v,
                            'tones': _gain_table(g, v), 'note': note})

        # tone phase
        if g.get('has_phase'):
            pstep = 360.0 / 2**g['b_phase']
            for v, note in [(0.0, 'tone phase zero'),
                            (90.0, 'tone phase quarter turn'),
                            (0.3 * pstep, 'tone phase quantization +0.3 step')]:
                out.append({**base, 'param': 'mux_phase', 'requested': v,
                            'tones': _phase_table(g, v), 'note': note})

        # how many tones the table may declare
        for count, note in [(n, 'tone table exactly n_tones'),
                            (n + 1, 'tone table over n_tones')]:
            out.append({**base, 'param': 'n_tones', 'requested': count,
                        'tones': tone_table(g, count),
                        'note': note})

        # the three lists must agree in length
        out.append({**base, 'param': 'tone_list_len', 'requested': n - 1,
                    'tones': tone_table(g, n, drop_gain=1),
                    'note': 'mux_gains shorter than mux_freqs'})

        # mask, against a table whose length is known
        for mask, req, note in [
                ([0], 0, 'mask names a declared tone'),
                ([0, 0], 0, 'mask names one tone twice'),
                ([n - 1], n - 1, 'mask names the last declared tone'),
                ([n], n, 'mask tone index == n_tones with n_tones declared')]:
            out.append({**base, 'param': 'mask', 'requested': req,
                        'tones': tone_table(g, n), 'mask': mask, 'note': note})

    return out


def _gain_table(g, v):
    t = tone_table(g, 2)
    t['gains'][0] = v
    return t


def _phase_table(g, v):
    t = tone_table(g, 2)
    t['phases'][0] = v
    return t
