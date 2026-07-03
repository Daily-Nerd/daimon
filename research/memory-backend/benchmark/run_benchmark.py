#!/usr/bin/env python3
"""
Context-as-Program Benchmark Runner

One-command script that ties the evaluation pipeline together:

    python benchmark/run_benchmark.py \
        --dataset benchmark/data/conversations.jsonl \
        --output benchmark/results/

Optional flags:
    --n-questions N         Number of questions per conversation (default: 5)
    --question-model MODEL  Model for question generation and grading
    --answer-model MODEL    Model for answering (default: kimi-k2.6)
    --max-conversations N   Limit number of conversations to evaluate
    --skip-qa               Skip QA evaluation (compression only)
    --generate-dataset      Generate the dataset if it doesn't exist
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root is on path so we can import prototype and benchmark modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate import (
    LLMClient,
    BenchmarkResult,
    ConversationResult,
    QAItem,
    aggregate_compression_results,
    aggregate_qa_results,
    aggregate_qa_by_method,
    generate_questions,
    answer_from_raw,
    answer_from_csl,
    answer_from_rag,
    grade_answer_against_source,
    generate_report,
    measure_compression,
    logger,
)
from benchmark.extractor import CSLExtractor
from prototype import MockCompressor
from benchmark.datasets import generate_dataset, load_dataset


def run_benchmark(
    dataset_path: str,
    output_dir: str,
    n_questions: int = 5,
    question_model: str = "gpt-5-via-cliproxy",
    answer_model: str = "kimi-k2.6",
    grade_model: str = "gpt-5-via-cliproxy",
    extractor_model: Optional[str] = None,
    max_conversations: Optional[int] = None,
    skip_qa: bool = False,
    generate_dataset_if_missing: bool = False,
) -> BenchmarkResult:
    """Run the full benchmark pipeline."""

    dataset_path_obj = Path(dataset_path)
    if not dataset_path_obj.exists():
        if generate_dataset_if_missing:
            logger.info("Dataset not found. Generating...")
            generate_dataset(str(dataset_path_obj))
        else:
            raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    conversations = load_dataset(str(dataset_path_obj))
    logger.info("Loaded %d conversations from %s", len(conversations), dataset_path)

    if max_conversations is not None:
        conversations = conversations[:max_conversations]
        logger.info("Limited to first %d conversations", len(conversations))

    output_dir_obj = Path(output_dir)
    output_dir_obj.mkdir(parents=True, exist_ok=True)

    llm_client = LLMClient(cache_dir=str(output_dir_obj / ".llm_cache"))
    if extractor_model:
        compressor = CSLExtractor(model=extractor_model)
        logger.info("Using LLM extractor: %s", extractor_model)
    else:
        compressor = MockCompressor()
        logger.info("Using MockCompressor (regex-based)")

    conv_results: List[ConversationResult] = []
    start_time = time.time()

    for idx, record in enumerate(conversations):
        conv_id = record.get("id", f"conv-{idx}")
        domain = record.get("domain", "unknown")
        raw_text = record.get("conversation", "")
        logger.info("[%d/%d] Evaluating %s (%s, ~%d tokens)", idx + 1, len(conversations), conv_id, domain, len(raw_text) // 4)

        # --- Generate CSL ---
        try:
            if extractor_model:
                extraction_result = compressor.extract(raw_text)
                if extraction_result.error:
                    raise RuntimeError(extraction_result.error)
                csl_program = extraction_result.program
            else:
                csl_program = compressor.compress(raw_text)
            csl_text = csl_program.to_csl()
        except Exception as exc:
            logger.error("Failed to compress %s: %s", conv_id, exc)
            # Create a dummy result with error and continue
            conv_results.append(
                ConversationResult(
                    conv_id=conv_id,
                    domain=domain,
                    compression=measure_compression(raw_text, ""),
                    errors=[f"Compression failed: {exc}"],
                )
            )
            continue

        # --- Compression evaluation ---
        comp_result = measure_compression(raw_text, csl_text)
        logger.info("  Compression: %.2fx (%d raw → %d CSL)", comp_result.ratio, comp_result.raw_tokens, comp_result.csl_tokens)

        qa_items: List[QAItem] = []
        errors: List[str] = []

        if not skip_qa:
            # --- Question generation ---
            try:
                questions = generate_questions(
                    raw_text,
                    n=n_questions,
                    llm_client=llm_client,
                    model=question_model,
                )
                logger.info("  Generated %d questions", len(questions))
            except Exception as exc:
                logger.error("  Failed to generate questions for %s: %s", conv_id, exc)
                errors.append(f"Question generation failed: {exc}")
                questions = []

            # --- QA loop: three answers at EQUAL token budget, blind-graded ---
            csl_budget = comp_result.csl_tokens
            for q_idx, question in enumerate(questions):
                logger.info("  Question %d/%d: %s", q_idx + 1, len(questions), question.id)
                try:
                    # raw = full conversation (upper bound, not equal-cost)
                    raw_ans = answer_from_raw(
                        question.text, raw_text, llm_client=llm_client, model=answer_model
                    )
                    # csl = the compressed program
                    csl_ans = answer_from_csl(
                        question.text, csl_text, llm_client=llm_client, model=answer_model
                    )
                    # rag = retrieval into the SAME budget as the CSL program
                    rag_ans = answer_from_rag(
                        question.text, raw_text, token_budget=csl_budget,
                        llm_client=llm_client, model=answer_model,
                    )

                    # Grade each answer BLIND against the source conversation.
                    # The grader is not told which method produced which answer.
                    raw_score = grade_answer_against_source(
                        raw_ans.text, question.text, raw_text, llm_client=llm_client, model=grade_model
                    )
                    csl_score = grade_answer_against_source(
                        csl_ans.text, question.text, raw_text, llm_client=llm_client, model=grade_model
                    )
                    rag_score = grade_answer_against_source(
                        rag_ans.text, question.text, raw_text, llm_client=llm_client, model=grade_model
                    )
                except Exception as exc:
                    logger.error("    QA failed for %s: %s", question.id, exc)
                    errors.append(f"QA failed for {question.id}: {exc}")
                    continue

                qa_items.append(
                    QAItem(
                        question=question,
                        raw_answer=raw_ans,
                        csl_answer=csl_ans,
                        score=csl_score,        # back-compat: top-level score = CSL
                        rag_answer=rag_ans,
                        raw_score=raw_score,
                        csl_score=csl_score,
                        rag_score=rag_score,
                    )
                )
                logger.info("    Blind overall — raw=%.2f csl=%.2f rag=%.2f (budget=%d tok)",
                            raw_score.overall, csl_score.overall, rag_score.overall, csl_budget)

        conv_results.append(
            ConversationResult(
                conv_id=conv_id,
                domain=domain,
                compression=comp_result,
                qa_items=qa_items,
                errors=errors,
            )
        )

        # --- Save intermediate results after each conversation ---
        intermediate = BenchmarkResult(
            conversations=conv_results,
            aggregate_compression=aggregate_compression_results(conv_results),
            aggregate_qa=aggregate_qa_results(conv_results),
            metadata={
                "dataset": dataset_path,
                "conversations_processed": len(conv_results),
                "total_conversations": len(conversations),
                "elapsed_seconds": round(time.time() - start_time, 1),
            },
        )
        with open(output_dir_obj / "results.json", "w", encoding="utf-8") as f:
            json.dump(intermediate.to_dict(), f, indent=2, ensure_ascii=False)

    # --- Final aggregation ---
    benchmark_result = BenchmarkResult(
        conversations=conv_results,
        aggregate_compression=aggregate_compression_results(conv_results),
        aggregate_qa=aggregate_qa_results(conv_results),
        metadata={
            "dataset": dataset_path,
            "conversations_processed": len(conv_results),
            "total_conversations": len(conversations),
            "elapsed_seconds": round(time.time() - start_time, 1),
            "extractor_model": extractor_model,
            "question_model": question_model,
            "answer_model": answer_model,
            "grade_model": grade_model,
            "n_questions": n_questions,
            "qa_by_method": aggregate_qa_by_method(conv_results),
        },
    )

    # --- Write outputs ---
    with open(output_dir_obj / "results.json", "w", encoding="utf-8") as f:
        json.dump(benchmark_result.to_dict(), f, indent=2, ensure_ascii=False)

    report_md = generate_report(benchmark_result)
    with open(output_dir_obj / "report.md", "w", encoding="utf-8") as f:
        f.write(report_md)

    logger.info("Benchmark complete. Results written to %s", output_dir_obj)
    logger.info("  JSON: %s", output_dir_obj / "results.json")
    logger.info("  Report: %s", output_dir_obj / "report.md")

    return benchmark_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Context-as-Program benchmark")
    parser.add_argument("--extractor-model", default=None, help="LLM model for CSL extraction (e.g. kimi-k2.6). If omitted, uses MockCompressor.")
    parser.add_argument("--dataset", default="benchmark/data/conversations.jsonl", help="Path to conversations JSONL")
    parser.add_argument("--output", default="benchmark/results/", help="Output directory")
    parser.add_argument("--n-questions", type=int, default=5, help="Questions per conversation")
    parser.add_argument("--question-model", default="gpt-5-via-cliproxy", help="Model for question generation")
    parser.add_argument("--answer-model", default="kimi-k2.6", help="Model for answering")
    parser.add_argument("--grade-model", default="gpt-5-via-cliproxy", help="Model for grading")
    parser.add_argument("--max-conversations", type=int, default=None, help="Limit number of conversations")
    parser.add_argument("--skip-qa", action="store_true", help="Skip QA evaluation (compression only)")
    parser.add_argument("--generate-dataset", action="store_true", help="Generate dataset if missing")
    args = parser.parse_args()

    try:
        run_benchmark(
            dataset_path=args.dataset,
            output_dir=args.output,
            n_questions=args.n_questions,
            question_model=args.question_model,
            answer_model=args.answer_model,
            grade_model=args.grade_model,
            extractor_model=args.extractor_model,
            max_conversations=args.max_conversations,
            skip_qa=args.skip_qa,
            generate_dataset_if_missing=args.generate_dataset,
        )
    except Exception as exc:
        logger.exception("Benchmark failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
