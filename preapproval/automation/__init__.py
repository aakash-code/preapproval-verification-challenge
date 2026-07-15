"""Deterministic, no-LLM engine.

Provides rule-based PDF extraction (``extract_rules``) and rule-based website
research (``research_rules``) so the full pipeline runs with zero Anthropic API
key. These are drop-in alternatives to ``preapproval.extract`` /
``preapproval.research`` and return the same models.
"""
