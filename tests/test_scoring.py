"""
test_scoring.py -- Tests for scoring.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import generate_job_id, get_connection, init_db, insert_job, get_job_by_id
from src.scoring import _parse_score_response, _load_profile, SCORING_PROMPT


SAMPLE_PROFILE_YAML = """
personal:
  name: "Jane Developer"
  email: "jane@example.com"
  phone: "+1 555-1234"
  location: "San Francisco, CA"

summary: |
  Experienced software engineer with 5+ years building distributed Python systems.

experience:
  - title: "Senior Software Engineer"
    company: "TechCorp"
    start: "2021-01"
    end: "present"
    bullets:
      - "Led migration of monolith to microservices using FastAPI and Docker"
      - "Reduced API response time by 40% via Redis caching"
    skills: [Python, FastAPI, Docker, Redis]

skills:
  languages: [Python, JavaScript, Go]
  frameworks: [FastAPI, Django, React]
  databases: [PostgreSQL, Redis]
  cloud: [AWS, Docker, Kubernetes]
"""

SAMPLE_JOB = {
    "id": "abc123def456abcd",
    "title": "Backend Engineer",
    "company": "StartupXYZ",
    "location": "Remote",
    "url": "https://example.com/jobs/123",
    "source": "linkedin",
    "description": (
        "We are looking for a Backend Engineer skilled in Python, FastAPI, "
        "PostgreSQL, and AWS. You will build scalable microservices and work "
        "closely with the product team."
    ),
    "status": "discovered",
    "date_discovered": "2025-01-01T00:00:00",
}

SAMPLE_CLAUDE_RESPONSE = json.dumps({
    "score": 8.5,
    "reasoning": "Strong match -- candidate's FastAPI and microservices experience directly aligns with requirements.",
    "key_matches": ["Python", "FastAPI", "PostgreSQL", "Microservices", "AWS"],
    "gaps": ["Kubernetes experience not mentioned in JD"],
    "ats_keywords": ["FastAPI", "microservices", "PostgreSQL", "AWS", "Python"],
})


class TestScoringPromptBuilding(unittest.TestCase):
    """Test that the scoring prompt is correctly assembled."""

    def test_prompt_contains_profile_yaml(self):
        """The scoring prompt should include the profile YAML."""
        prompt = SCORING_PROMPT.format(
            profile_yaml=SAMPLE_PROFILE_YAML,
            title=SAMPLE_JOB["title"],
            company=SAMPLE_JOB["company"],
            location=SAMPLE_JOB["location"],
            description=SAMPLE_JOB["description"],
        )
        self.assertIn("Jane Developer", prompt)
        self.assertIn("FastAPI", prompt)
        self.assertIn("microservices", prompt)

    def test_prompt_contains_job_details(self):
        """The scoring prompt should include job title, company, and description."""
        prompt = SCORING_PROMPT.format(
            profile_yaml=SAMPLE_PROFILE_YAML,
            title="Senior Python Engineer",
            company="Acme Corp",
            location="Berlin",
            description="We need Python, Django, PostgreSQL skills.",
        )
        self.assertIn("Senior Python Engineer", prompt)
        self.assertIn("Acme Corp", prompt)
        self.assertIn("Django", prompt)
        self.assertIn("Berlin", prompt)

    def test_prompt_requests_json_format(self):
        """The scoring prompt should request JSON output."""
        prompt = SCORING_PROMPT.format(
            profile_yaml="",
            title="",
            company="",
            location="",
            description="",
        )
        self.assertIn("JSON", prompt)
        self.assertIn('"score"', prompt)
        self.assertIn('"reasoning"', prompt)


class TestParseScoreResponse(unittest.TestCase):
    """Test JSON parsing of Claude's score responses."""

    def test_parse_clean_json(self):
        """Should parse a clean JSON response."""
        result = _parse_score_response(SAMPLE_CLAUDE_RESPONSE)
        self.assertEqual(result["score"], 8.5)
        self.assertEqual(result["reasoning"][:20], "Strong match -- cand")
        self.assertIn("Python", result["key_matches"])
        self.assertIn("FastAPI", result["ats_keywords"])

    def test_parse_json_with_code_fence(self):
        """Should strip markdown code fences before parsing."""
        fenced = f"```json\n{SAMPLE_CLAUDE_RESPONSE}\n```"
        result = _parse_score_response(fenced)
        self.assertEqual(result["score"], 8.5)

    def test_parse_json_with_preamble(self):
        """Should find JSON object embedded in surrounding text."""
        with_preamble = f"Here is my analysis:\n{SAMPLE_CLAUDE_RESPONSE}\nLet me know if you need more."
        result = _parse_score_response(with_preamble)
        self.assertEqual(result["score"], 8.5)

    def test_parse_invalid_json_raises(self):
        """Should raise ValueError for completely unparseable responses."""
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            _parse_score_response("This is not JSON at all, just text.")

    def test_score_is_float(self):
        """Score should be convertible to float."""
        result = _parse_score_response(SAMPLE_CLAUDE_RESPONSE)
        score = float(result["score"])
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 10)


class TestScoreStoredInDB(unittest.TestCase):
    """Test that scores are correctly persisted to the database."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        init_db(self.db_path)
        conn = get_connection(self.db_path)
        insert_job(conn, SAMPLE_JOB)
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_score_written_to_db(self):
        """After scoring, the job should have score and status='scored' in DB."""
        from src.database import update_job

        conn = get_connection(self.db_path)
        update_job(
            conn,
            SAMPLE_JOB["id"],
            score=8.5,
            score_reasoning="Strong match",
            keywords_matched='["Python", "FastAPI"]',
            status="scored",
        )
        conn.close()

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()

        self.assertIsNotNone(job)
        self.assertAlmostEqual(float(job["score"]), 8.5)
        self.assertEqual(job["status"], "scored")
        self.assertEqual(job["score_reasoning"], "Strong match")

    def test_keywords_stored_as_json_string(self):
        """Keywords should be stored as a JSON string in the DB."""
        from src.database import update_job

        keywords = ["Python", "FastAPI", "PostgreSQL"]
        conn = get_connection(self.db_path)
        update_job(conn, SAMPLE_JOB["id"], keywords_matched=json.dumps(keywords))
        conn.close()

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()

        stored = json.loads(job["keywords_matched"])
        self.assertEqual(stored, keywords)

    @patch("src.scoring._call_claude")
    def test_run_scoring_mocked_api(self, mock_call_claude):
        """run_scoring should call Claude and update the DB."""
        mock_call_claude.return_value = SAMPLE_CLAUDE_RESPONSE

        config = {
            "claude_api_key": "sk-test-key",
            "scoring": {"min_score": 5.0, "max_jobs_per_run": 50},
        }

        # anthropic is imported lazily inside run_scoring; patch the builtin import
        import unittest.mock as _mock
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=SAMPLE_CLAUDE_RESPONSE)]
        )

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.scoring import run_scoring
            results = run_scoring(config, self.db_path, limit=50)

        # Verify DB was updated
        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()

        self.assertIsNotNone(job)
        self.assertAlmostEqual(float(job["score"]), 8.5, places=1)
        self.assertEqual(job["status"], "scored")


class TestGenerateJobId(unittest.TestCase):
    """Test the generate_job_id helper."""

    def test_deterministic(self):
        """Same URL should always produce the same ID."""
        url = "https://example.com/jobs/12345"
        id1 = generate_job_id(url)
        id2 = generate_job_id(url)
        self.assertEqual(id1, id2)

    def test_length(self):
        """ID should be exactly 16 characters."""
        url = "https://example.com/jobs/abc"
        job_id = generate_job_id(url)
        self.assertEqual(len(job_id), 16)

    def test_different_urls_produce_different_ids(self):
        """Different URLs should produce different IDs (with very high probability)."""
        id1 = generate_job_id("https://example.com/jobs/1")
        id2 = generate_job_id("https://example.com/jobs/2")
        self.assertNotEqual(id1, id2)

    def test_hexadecimal_chars_only(self):
        """ID should only contain hexadecimal characters."""
        import re
        url = "https://linkedin.com/jobs/view/12345678"
        job_id = generate_job_id(url)
        self.assertRegex(job_id, r"^[0-9a-f]{16}$")


if __name__ == "__main__":
    unittest.main()
