#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from typing import Optional

import requests
from dotenv import load_dotenv


def load_env():
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("MODEL")
    if not api_key or api_key == "your_key_here":
        print("Error: OPENROUTER_API_KEY not set in .env")
        sys.exit(1)
    if not model:
        model = "meta-llama/llama-3.3-70b-instruct:free"
    return api_key, model


def load_prompt_template(prompt_file: str) -> str:
    with open(prompt_file, "r") as f:
        return f.read()


def load_dataset(jsonl_file: str, limit: int):
    entries = []
    with open(jsonl_file, "r") as f:
        for line in f:
            entries.append(json.loads(line))
            if len(entries) >= limit:
                break
    return entries


def call_llm(api_key: str, model: str, prompt: str, timeout: int = 30) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "Equation Equivalence Tester"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        return "__TIMEOUT__"
    except Exception as e:
        return f"__ERROR__: {str(e)}"


def parse_response(response: str) -> Optional[str]:
    import re
    lines = response.strip().split("\n")
    for line in reversed(lines):
        line = line.strip().upper()
        if line == "TRUE":
            return "true"
        elif line == "FALSE":
            return "false"
        words = line.split()
        if words:
            last_word = words[-1].strip(".:,;")
            if last_word == "TRUE":
                return "true"
            elif last_word == "FALSE":
                return "false"
    match = re.search(r'\b(TRUE|FALSE)\b', response.upper())
    if match:
        return match.group(1).lower()
    return None


def log_error(entry_id: str, response: str, status: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_chars = response[-200:] if len(response) > 200 else response
    with open("logs.txt", "a") as f:
        f.write(f"[{timestamp}] {entry_id} - {status}\n")
        f.write(f"Response (last 200 chars): {last_chars}\n")
        f.write("-" * 50 + "\n")


def evaluate(api_key: str, model: str, entries: list, prompt_template: str):
    results = []
    correct = 0
    incorrect = 0
    errors = 0

    total = len(entries)
    print(f"Testing {total} entries...\n")

    for i, entry in enumerate(entries):
        eq1 = entry["equation1"]
        eq2 = entry["equation2"]
        expected = entry["answer"]

        prompt = prompt_template.replace("{{eq1}}", eq1).replace("{{eq2}}", eq2)

        response = call_llm(api_key, model, prompt)
        predicted = parse_response(response)

        if predicted is None:
            status = "PARSE_ERROR"
            errors += 1
            log_error(entry["id"], response, status)
        elif predicted == str(expected).lower():
            status = "PASS"
            correct += 1
        else:
            status = "FAIL"
            incorrect += 1

        result = {
            "id": entry["id"],
            "index": entry["index"],
            "eq1": eq1,
            "eq2": eq2,
            "expected": expected,
            "predicted": predicted,
            "status": status,
            "response": response
        }
        results.append(result)

        bar_len = 30
        filled = int(bar_len * (i + 1) / total)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"[{bar}] {i + 1}/{total} - {status}")

    return results, correct, incorrect, errors


def print_stats(total: int, correct: int, incorrect: int, errors: int):
    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Total tested:    {total}")
    print(f"Correct:         {correct}")
    print(f"Incorrect:       {incorrect}")
    print(f"Parse errors:    {errors}")
    print(f"Accuracy:        {correct / (total - errors) * 100:.1f}%" if (total - errors) > 0 else "N/A")
    print("=" * 50)


def save_results(results: list, correct: int, incorrect: int, errors: int):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"results_{timestamp}.json"

    output = {
        "timestamp": timestamp,
        "summary": {
            "total": len(results),
            "correct": correct,
            "incorrect": incorrect,
            "errors": errors,
            "accuracy": correct / (len(results) - errors) * 100 if (len(results) - errors) > 0 else 0
        },
        "results": results
    }

    with open(filename, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to {filename}")
    return filename


def generate_distilled_prompt(api_key: str, distill_model: str, base_prompt: str, results: list, timeout: int = 60):
    pass_results = [r for r in results if r["status"] == "PASS"]
    fail_results = [r for r in results if r["status"] == "FAIL"]

    prompt = """You are a prompt engineer. Analyze these examples from an LLM evaluation on equational implication over magmas.

BASE PROMPT:
"""
    prompt += base_prompt + "\n\n"

    prompt += f"PASS EXAMPLES (model got these RIGHT - {len(pass_results)} examples):\n"
    for r in pass_results[:10]:
        prompt += f'- eq1="{r["eq1"]}", eq2="{r["eq2"]}", expected={r["expected"]}, model_response="{r["response"][:500] if isinstance(r["response"], str) else r["response"]}"\n'

    prompt += f"\nFAIL EXAMPLES (model got these WRONG - {len(fail_results)} examples):\n"
    for r in fail_results[:10]:
        prompt += f'- eq1="{r["eq1"]}", eq2="{r["eq2"]}", expected={r["expected"]}, predicted={r["predicted"]}, model_response="{r["response"][:500] if isinstance(r["response"], str) else r["response"]}"\n'

    prompt += """
Analyze the patterns in successful vs failed reasoning traces. Generate an improved prompt that:
1. Keeps the core task (equational implication over magmas)
2. Incorporates successful reasoning patterns from PASS examples
3. Uses FAIL examples to create constraints/negative guidance
4. Maintains {{eq1}} and {{eq2}} placeholders

Output only the new prompt, no explanation. Start directly with the prompt content.
"""

    print("\n" + "=" * 50)
    print("GENERATING DISTILLED PROMPT...")
    print("=" * 50 + "\n")

    response = call_llm(api_key, distill_model, prompt, timeout=timeout)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"distilled_prompt_{timestamp}.md"

    with open(filename, "w") as f:
        f.write(response)

    print(f"Distilled prompt saved to {filename}")
    return filename


def main():
    parser = argparse.ArgumentParser(description="Evaluate LLM on equation equivalence")
    parser.add_argument("--entries", type=int, default=100, help="Number of entries to test")
    parser.add_argument("--prompt", default="baseprompt.md", help="Prompt template file")
    parser.add_argument("--dataset", default="normal.jsonl", help="Dataset file")
    parser.add_argument("--timeout", type=int, default=30, help="API timeout in seconds")
    parser.add_argument("--distill", action="store_true", help="Generate distilled prompt after evaluation")
    parser.add_argument("--distill-model", default="x-ai/grok-4.20-multi-agent-beta", help="Model to use for distillation")
    args = parser.parse_args()

    api_key, model = load_env()
    print(f"Using model: {model}\n")

    prompt_template = load_prompt_template(args.prompt)
    entries = load_dataset(args.dataset, args.entries)

    results, correct, incorrect, errors = evaluate(api_key, model, entries, prompt_template)

    print_stats(len(results), correct, incorrect, errors)
    save_results(results, correct, incorrect, errors)

    if args.distill:
        generate_distilled_prompt(api_key, args.distill_model, prompt_template, results, args.timeout)


if __name__ == "__main__":
    main()
