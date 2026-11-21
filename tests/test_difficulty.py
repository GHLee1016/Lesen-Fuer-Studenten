from backend.analysis import AnalysisParams, Fixation, Word, difficulty

LH = 44.0  # line height


def layout():
    """3 sentences of 8 words each, one sentence per line, words 80px apart."""
    words = []
    for s in range(3):
        for k in range(8):
            words.append(Word(x=20 + k * 90, y=s * LH + 10, w=80, h=24, s=s))
    return words


def fix(t, word, words, d=220):
    w = words[word]
    return Fixation(start=t, end=t + d, x=w.x + w.w / 2, y=w.y + w.h / 2)


def read_through(words, sentences=(0, 1, 2), t0=0, d=220):
    out, t = [], t0
    for s in sentences:
        for i, w in enumerate(words):
            if w.s == s:
                out.append(fix(t, i, words, d))
                t += d + 30
    return out, t


def test_even_reading_has_no_difficulty():
    words = layout()
    fx, _ = read_through(words)
    d = difficulty(fx, words, LH)
    assert d["difficult_sentences"] == []
    assert d["difficult_words"] == []
    assert d["penalty"] == 0
    assert d["word_hits"] == len(fx)


def test_slow_sentence_is_flagged_and_penalised():
    words = layout()
    a, t = read_through(words, (0,))
    b, t = read_through(words, (1,), t, d=700)  # ~3x slower
    c, t = read_through(words, (2,), t)
    d = difficulty(a + b + c, words, LH)
    assert d["difficult_sentences"] == [1]
    assert d["sentences"][1]["reasons"] == ["slow"]
    assert d["penalty"] >= round(40 * 8 / 24)


def test_rereading_a_sentence_is_flagged():
    words = layout()
    fx, t = read_through(words, (0, 1, 2))
    for _ in range(2):  # jump back to sentence 0, then forward again
        back, t = read_through(words, (0,), t)
        fwd, t = read_through(words, (2,), t)
        fx += back + fwd
    d = difficulty(fx, words, LH)
    assert 0 in d["difficult_sentences"]
    assert "reread" in d["sentences"][0]["reasons"]


def test_long_dwell_on_one_word_is_a_candidate():
    words = layout()
    fx, t = read_through(words, (0,))
    fx.append(fix(t, 10, words, d=1800))  # staring at word 10 in sentence 1
    more, _ = read_through(words, (2,), t + 1900)
    d = difficulty(fx + more, words, LH)
    assert [w["i"] for w in d["difficult_words"]] == [10]
    assert d["penalty"] >= 1


def test_off_text_fixations_are_ignored():
    words = layout()
    fx = [Fixation(0, 500, 400, 400)]  # far below the text
    d = difficulty(fx, words, LH)
    assert d["word_hits"] == 0 and d["penalty"] == 0


def test_penalty_is_capped():
    words = layout()
    p = AnalysisParams(max_penalty=5)
    fx, t = read_through(words, (0, 1))
    slow, _ = read_through(words, (2,), t, d=900)  # would cost 13 points uncapped
    d = difficulty(fx + slow, words, LH, p)
    assert d["difficult_sentences"] == [2]
    assert d["penalty"] == 5
