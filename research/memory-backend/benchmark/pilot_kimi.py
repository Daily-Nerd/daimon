#!/usr/bin/env python3
"""Quick pilot script: run Kimi extraction + QA on conversations 2-6 (skip problematic #1)."""

import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.extractor import CSLExtractor
from benchmark.evaluate import (
    LLMClient, measure_compression, generate_questions,
    answer_from_raw, answer_from_csl, answer_from_rag,
    grade_answer_against_source,
)
from benchmark.datasets import load_dataset

def main():
    dataset = load_dataset("benchmark/data/conversations.jsonl")
    # Skip conversation 0 (medical_case - Kimi fails on it)
    conversations = dataset[1:6]
    print(f"Pilot: {len(conversations)} conversations (indices 1-5)")

    extractor = CSLExtractor(model="kimi-k2.6", max_retries=1)
    llm = LLMClient(cache_dir="benchmark/results/pilot-kimi/.llm_cache")

    results = []
    for idx, record in enumerate(conversations):
        conv_id = record["id"]
        raw = record["conversation"]
        print(f"\n[{idx+1}/{len(conversations)}] {conv_id}")

        # Extract
        print("  Extracting...")
        t0 = time.time()
        ext = extractor.extract(raw)
        t1 = time.time()
        if ext.error:
            print(f"  EXTRACTION FAILED: {ext.error}")
            results.append({"conv_id": conv_id, "error": ext.error})
            continue
        comp = measure_compression(raw, ext.program.to_csl())
        print(f"  Compression: {comp.ratio:.2f}x ({comp.raw_tokens} -> {comp.csl_tokens})")
        print(f"  Statements: {len(ext.program)}  Time: {t1-t0:.1f}s  Cost: ${ext.cost_usd:.4f}")

        # QA
        print("  Generating questions...")
        try:
            questions = generate_questions(raw, n=3, llm_client=llm, model="kimi-k2.6")
            print(f"  Questions: {len(questions)}")
        except Exception as e:
            print(f"  QA FAILED: {e}")
            results.append({"conv_id": conv_id, "compression": comp.ratio, "error": str(e)})
            continue

        csl_text = ext.program.to_csl()
        csl_budget = comp.csl_tokens
        qa_scores = []
        for q in questions:
            print(f"    Q: {q.text[:60]}...")
            # Three answers at EQUAL token budget; RAG fits the CSL budget.
            raw_ans = answer_from_raw(q.text, raw, llm_client=llm, model="kimi-k2.6")
            csl_ans = answer_from_csl(q.text, csl_text, llm_client=llm, model="kimi-k2.6")
            rag_ans = answer_from_rag(q.text, raw, token_budget=csl_budget, llm_client=llm, model="kimi-k2.6")
            # Blind grade each against the source conversation.
            raw_s = grade_answer_against_source(raw_ans.text, q.text, raw, llm_client=llm, model="kimi-k2.6")
            csl_s = grade_answer_against_source(csl_ans.text, q.text, raw, llm_client=llm, model="kimi-k2.6")
            rag_s = grade_answer_against_source(rag_ans.text, q.text, raw, llm_client=llm, model="kimi-k2.6")
            qa_scores.append({
                "question": q.text,
                "raw_overall": raw_s.overall,
                "csl_overall": csl_s.overall,
                "rag_overall": rag_s.overall,
                "csl_accuracy": csl_s.accuracy,
                "csl_completeness": csl_s.completeness,
            })
            print(f"      Blind overall — raw={raw_s.overall:.2f} csl={csl_s.overall:.2f} rag={rag_s.overall:.2f}")

        results.append({
            "conv_id": conv_id, "compression": comp.ratio,
            "raw_tokens": comp.raw_tokens, "csl_tokens": comp.csl_tokens,
            "statements": len(ext.program), "time": t1-t0,
            "qa": qa_scores,
        })

    # Save
    out = "benchmark/results/pilot-kimi/pilot_results.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out}")

    # Summary
    print("\n=== PILOT SUMMARY ===")
    successes = [r for r in results if "error" not in r]
    print(f"Successes: {len(successes)}/{len(results)}")
    if successes:
        avg_ratio = sum(r["compression"] for r in successes) / len(successes)
        print(f"Avg compression: {avg_ratio:.2f}x")
        all_qa = [s for r in successes for s in r.get("qa", [])]
        if all_qa:
            n = len(all_qa)
            raw_o = sum(q["raw_overall"] for q in all_qa) / n
            csl_o = sum(q["csl_overall"] for q in all_qa) / n
            rag_o = sum(q["rag_overall"] for q in all_qa) / n
            print(f"Blind overall (equal budget) — RAW={raw_o:.2f}  CSL={csl_o:.2f}  RAG={rag_o:.2f}")
            verdict = "CSL beats RAG" if csl_o > rag_o else "RAG beats CSL" if rag_o > csl_o else "CSL ties RAG"
            print(f"Verdict: {verdict} (Δ={csl_o - rag_o:+.2f})")

if __name__ == "__main__":
    main()
