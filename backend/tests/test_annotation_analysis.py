from pathlib import Path

from repo_reviewer.annotation_analysis import summarize_annotations


def test_summarize_annotations(tmp_path: Path) -> None:
    annotation_sheet = tmp_path / "annotation-sheet.csv"
    annotation_sheet.write_text(
        "run_id,repo_name,method,provider,model,file,line,severity,issue,suggestion,correctness,actionability,severity_calibration,duplication,scope,top5_usefulness,annotator_notes\n"
        "r1,demo,full,groq,m,a.py,1,low,i,s,correct,actionable,appropriate,unique,code_level,5,\n"
        "r2,demo,single_agent,groq,m,b.py,2,medium,i,s,incorrect,not_actionable,too_high,duplicate,code_level,2,\n",
        encoding="utf-8",
    )
    csv_path, json_path = summarize_annotations(annotation_sheet, tmp_path / "summary")
    assert csv_path.exists()
    assert json_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert "full" in content
    assert "single_agent" in content
    latex_content = (tmp_path / "summary" / "annotation-summary.tex").read_text(encoding="utf-8")
    assert "\\begin{tabular}" in latex_content
