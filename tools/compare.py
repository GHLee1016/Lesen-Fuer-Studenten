"""Agreement between automatic difficulty detection and students' own reports.

    DB_PATH=data/lesen.db python -m tools.compare            # summary
    DB_PATH=data/lesen.db python -m tools.compare --whatif   # also rank threshold candidates

Only sessions where the student answered the self-report step count.
Mouse-demo sessions are left out (add --mouse to include them).
"""

from __future__ import annotations

import sys

from backend import db, validation


def pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def ci(v) -> str:
    return "" if not v else f" ({v[0] * 100:.0f}–{v[1] * 100:.0f}%)"


def stats_row(name: str, s: dict) -> str:
    return (f"| {name} | {s['sessions']} | {pct(s['precision'])}{ci(s['precision_ci'])} | {pct(s['recall'])}{ci(s['recall_ci'])} "
            f"| {'–' if s['f1'] is None else s['f1']} | {s['no_report_sessions']} | {'–' if s['no_report_flags'] is None else s['no_report_flags']} "
            f"| {'–' if s['no_report_penalty'] is None else s['no_report_penalty']} |")


HEAD = ("| 구분 | 세션 | 정밀도 (95% CI) | 재현율 (95% CI) | F1 | '어려움 없음' 세션 | 그 세션의 평균 표시 | 그 세션의 평균 감점 |\n"
        "|---|---|---|---|---|---|---|---|")


def main(argv: list[str]) -> None:
    db.init()
    sessions = validation.labelled_sessions(include_mouse="--mouse" in argv)
    if not sessions:
        print("자기 평가가 있는 세션이 없어요. 학생이 결과를 보기 전에 '어려웠던 문장'을 표시한 세션이 필요해요.")
        return
    rep = validation.summarize(sessions)
    print(HEAD)
    print(stats_row("전체", rep["overall"]))
    for b in rep["by_bucket"]:
        print(stats_row(f"보정 오차 {b['bucket']}", b))
    a = rep["after_results"]
    print(f"\n결과를 본 뒤 자동 표시를 받아들인 문장 {a['added_from_auto']}개, 자기 표시를 지운 문장 {a['removed_own']}개")
    if "--whatif" in argv:
        print("\n기준 후보 (위가 학생 표시와 가장 잘 맞음)\n")
        print("| 후보 | slow_factor | return_min_lines | return_min_ms | F1 | 정밀도 | 재현율 | '어려움 없음' 세션 표시 |\n|---|---|---|---|---|---|---|---|")
        for r in validation.whatif(sessions):
            p = r["params"]
            tag = "현재" if r["current"] else ""
            print(f"| {tag} | {p['slow_factor']} | {p['return_min_lines']} | {p['return_min_ms']:.0f} | {r['f1']} | {pct(r['precision'])} | {pct(r['recall'])} | {r['no_report_flags']} |")
    if len(sessions) < 20:
        print(f"\n참고: 세션이 {len(sessions)}개뿐이라 신뢰구간이 넓어요. 20개 이상 모이면 비교가 믿을 만해져요.")


if __name__ == "__main__":
    main(sys.argv[1:])
