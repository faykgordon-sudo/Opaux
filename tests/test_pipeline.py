"""
test_pipeline.py -- Integration tests for the full pipeline.

Tests the full flow: insert job -> score (mocked Claude) -> tailor (mocked Claude)
-> verify final DB state.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import (
    generate_job_id,
    get_connection,
    get_job_by_id,
    init_db,
    insert_job,
    update_job,
    get_unscored_jobs,
    get_all_jobs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_JOB_URL = "https://jobs.example.com/pipeline-test/1234"
FAKE_JOB_ID = generate_job_id(FAKE_JOB_URL)

FAKE_JOB = {
    "id": FAKE_JOB_ID,
    "title": "Full Stack Python Developer",
    "company": "Innovate Inc",
    "location": "Berlin, Germany",
    "url": FAKE_JOB_URL,
    "source": "indeed",
    "description": (
        "Join our team as a Full Stack Python Developer. "
        "Required: Python, Django, React, PostgreSQL, Docker, AWS. "
        "You will lead feature development and work in an agile team."
    ),
    "status": "discovered",
    "date_discovered": "2025-03-01T10:00:00",
}

MOCK_SCORE_RESPONSE = json.dumps({
    "score": 7.8,
    "reasoning": "Strong Python/Django match. React and AWS experience present. Good fit.",
    "key_matches": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS"],
    "gaps": ["No specific mention of GraphQL"],
    "ats_keywords": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS", "full stack"],
})

MOCK_KEYWORD_RESPONSE = json.dumps({
    "must_have": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS"],
    "nice_to_have": ["GraphQL", "CI/CD"],
    "company_specific": ["agile", "feature development"],
    "action_verbs": ["develop", "deploy", "lead", "build"],
})

MOCK_TAILORING_RESPONSE = json.dumps({
    "must_have": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS"],
    "nice_to_have": ["GraphQL", "CI/CD"],
    "company_specific": ["agile", "feature development"],
    "keyword_mapping": {
        "Python": "5+ years Python development",
        "Django": "Built REST APIs with Django",
        "React": "Frontend development with React",
    },
    "tailored_summary": (
        "Full Stack Python developer with 5+ years building Django and React applications. "
        "Experienced with PostgreSQL, Docker, and AWS deployment."
    ),
    "tailored_bullets": {
        "Senior Software Engineer": [
            "Developed full stack features using Django REST framework and React frontend",
            "Deployed containerized applications using Docker on AWS ECS",
            "Reduced page load time by 35% through React optimization and PostgreSQL query tuning",
        ],
        "Software Engineer": [
            "Built scalable Django APIs serving 200k daily requests on PostgreSQL",
            "Implemented agile sprint processes, reducing delivery time by 25%",
        ],
    },
    "skills_to_highlight": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS"],
    "ats_keyword_coverage": {"covered": ["Python", "Django", "React", "PostgreSQL", "Docker", "AWS"], "missing": []},
    "keyword_gaps": [],
    "estimated_ats_score": 0.92,
})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_mock_client(score_response: str = MOCK_SCORE_RESPONSE,
                      tailor_response: str = MOCK_TAILORING_RESPONSE):
    """Create a mock Anthropic client that returns pre-defined responses."""
    mock_client = MagicMock()
    def _make_msg(text):
        m = MagicMock()
        m.content = [MagicMock(text=text)]
        return m
    # Sequence: scoring | keyword extraction | tailoring (ATS 0.92, no refinement)
    mock_client.messages.create.side_effect = [
        _make_msg(score_response),
        _make_msg(MOCK_KEYWORD_RESPONSE),
        _make_msg(tailor_response),
    ]
    return mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDatabaseBasicOperations(unittest.TestCase):
    """Test that core DB operations work correctly for pipeline usage."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        init_db(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_insert_and_retrieve_job(self):
        """A inserted job should be retrievable by ID."""
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)
        conn.close()

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertIsNotNone(job)
        self.assertEqual(job["title"], "Full Stack Python Developer")
        self.assertEqual(job["company"], "Innovate Inc")
        self.assertEqual(job["status"], "discovered")

    def test_upsert_does_not_overwrite(self):
        """Inserting the same job twice should not overwrite the first insert."""
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)
        conn.close()

        # Update the job's status
        conn = get_connection(self.db_path)
        update_job(conn, FAKE_JOB_ID, status="scored", score=8.0)
        conn.close()

        # Try to insert again with original data
        modified_job = {**FAKE_JOB, "title": "Should Not Overwrite"}
        conn = get_connection(self.db_path)
        insert_job(conn, modified_job)  # INSERT OR IGNORE -- should be a no-op
        conn.close()

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        # Title should be original, not the "Should Not Overwrite" one
        self.assertEqual(job["title"], "Full Stack Python Developer")
        # Status and score from the update should still be there
        self.assertEqual(job["status"], "scored")
        self.assertAlmostEqual(float(job["score"]), 8.0)

    def test_get_unscored_jobs_empty(self):
        """Should return empty list when no jobs are present."""
        conn = get_connection(self.db_path)
        jobs = get_unscored_jobs(conn)
        conn.close()
        self.assertEqual(jobs, [])

    def test_get_unscored_jobs_returns_only_unscored(self):
        """Should not return jobs that already have a score."""
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)  # No score
        # Insert a scored job
        scored_job = {
            **FAKE_JOB,
            "id": generate_job_id("https://example.com/other"),
            "url": "https://example.com/other",
            "score": 7.5,
            "status": "scored",
        }
        insert_job(conn, scored_job)
        conn.close()

        conn = get_connection(self.db_path)
        unscored = get_unscored_jobs(conn)
        conn.close()

        self.assertEqual(len(unscored), 1)
        self.assertEqual(unscored[0]["id"], FAKE_JOB_ID)

    def test_get_all_jobs_with_status_filter(self):
        """get_all_jobs with status filter should return only matching jobs."""
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)
        scored_job = {
            **FAKE_JOB,
            "id": generate_job_id("https://example.com/scored"),
            "url": "https://example.com/scored",
        }
        insert_job(conn, scored_job)
        update_job(conn, scored_job["id"], status="scored")
        conn.close()

        conn = get_connection(self.db_path)
        discovered = get_all_jobs(conn, status="discovered")
        scored = get_all_jobs(conn, status="scored")
        conn.close()

        self.assertEqual(len(discovered), 1)
        self.assertEqual(len(scored), 1)


class TestScoringIntegration(unittest.TestCase):
    """Integration test for the scoring step."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        init_db(self.db_path)
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)
        conn.close()

        self.config = {
            "claude_api_key": "sk-test-key-for-mocking",
            "scoring": {"min_score": 5.0, "max_jobs_per_run": 50},
        }

    def tearDown(self):
        os.unlink(self.db_path)

    def test_scoring_updates_db(self):
        """Scoring should update the job's score and status in DB."""
        import unittest.mock as _mock
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=MOCK_SCORE_RESPONSE)]
        )

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.scoring import run_scoring
            results = run_scoring(self.config, self.db_path, limit=10)

        self.assertEqual(len(results), 1)

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertIsNotNone(job)
        self.assertAlmostEqual(float(job["score"]), 7.8, places=1)
        self.assertEqual(job["status"], "scored")
        self.assertIsNotNone(job["score_reasoning"])
        self.assertIsNotNone(job["keywords_matched"])

    def test_scoring_respects_limit(self):
        """Scoring should not process more jobs than the limit."""
        import unittest.mock as _mock
        # Insert 5 more unscored jobs
        conn = get_connection(self.db_path)
        for i in range(5):
            extra_job = {
                **FAKE_JOB,
                "id": generate_job_id(f"https://example.com/job/{i}"),
                "url": f"https://example.com/job/{i}",
            }
            insert_job(conn, extra_job)
        conn.close()

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock(content=[MagicMock(text=MOCK_SCORE_RESPONSE)])
        mock_client.messages.create.return_value = mock_response

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.scoring import run_scoring
            results = run_scoring(self.config, self.db_path, limit=3)

        # Should have scored at most 3 jobs
        self.assertLessEqual(len(results), 3)
        # Claude should have been called at most 3 times
        self.assertLessEqual(mock_client.messages.create.call_count, 3)


class TestFullPipelineIntegration(unittest.TestCase):
    """
    Full integration test: insert fake job -> score -> tailor -> verify DB state.
    All Claude API calls are mocked.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

        self.output_dir = tempfile.mkdtemp()

        init_db(self.db_path)
        conn = get_connection(self.db_path)
        insert_job(conn, FAKE_JOB)
        conn.close()

        self.config = {
            "claude_api_key": "sk-test-key-for-mocking",
            "scoring": {"min_score": 5.0, "max_jobs_per_run": 50},
            "application": {"default_format": "american", "default_lang": "en"},
            "output": {"dir": self.output_dir},
            "database": {"path": self.db_path},
        }

    def tearDown(self):
        os.unlink(self.db_path)
        # Clean up output dir
        import shutil
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_step1_score_job(self):
        """Step 1: Score the fake job and verify DB state."""
        import unittest.mock as _mock
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=MOCK_SCORE_RESPONSE)]
        )

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.scoring import run_scoring
            results = run_scoring(self.config, self.db_path, limit=10)

        self.assertGreater(len(results), 0)

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertEqual(job["status"], "scored")
        self.assertAlmostEqual(float(job["score"]), 7.8, places=1)
        self.assertIsNotNone(job["score_reasoning"])

        keywords = json.loads(job["keywords_matched"])
        self.assertIn("Python", keywords)
        self.assertIn("Django", keywords)

    @patch("src.tailoring._load_profile")
    def test_step2_tailor_job(self, mock_load_profile):
        """Step 2: Tailor the scored job and verify .docx is created."""
        import unittest.mock as _mock
        import yaml as _yaml

        # Pre-score the job
        conn = get_connection(self.db_path)
        update_job(conn, FAKE_JOB_ID, score=7.8, status="scored",
                   keywords_matched=json.dumps(["Python", "Django", "React"]))
        conn.close()

        # Setup profile mock
        sample_profile_dict = {
            "personal": {
                "name": "Jane Developer",
                "email": "jane@example.com",
                "phone": "+1 555-1234",
                "location": "San Francisco, CA",
            },
            "summary": "Experienced Python developer.",
            "experience": [
                {
                    "title": "Senior Software Engineer",
                    "company": "TechCorp",
                    "start": "2021-01",
                    "end": "present",
                    "bullets": ["Built APIs with Django", "Led team of 4"],
                    "skills": ["Python", "Django", "Docker"],
                },
                {
                    "title": "Software Engineer",
                    "company": "WebAgency",
                    "start": "2018-06",
                    "end": "2020-12",
                    "bullets": ["Developed React frontend"],
                    "skills": ["React", "JavaScript"],
                },
            ],
            "skills": {
                "languages": ["Python", "JavaScript"],
                "frameworks": ["Django", "React"],
                "databases": ["PostgreSQL"],
                "cloud": ["AWS", "Docker"],
            },
            "certifications": [],
            "languages": [{"language": "English", "level": "Native", "cefr": "C2"}],
        }
        mock_load_profile.return_value = (_yaml.dump(sample_profile_dict), sample_profile_dict)

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=MOCK_TAILORING_RESPONSE)]
        )

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.tailoring import run_tailoring
            try:
                output_path = run_tailoring(
                    self.config, self.db_path, FAKE_JOB_ID, format="american", lang="en"
                )
            except ImportError:
                self.skipTest("python-docx not installed -- skipping docx generation test")

        # Verify DB was updated
        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertEqual(job["status"], "tailored")
        self.assertIsNotNone(job["cv_path"])
        self.assertAlmostEqual(float(job["ats_score"]), 0.92, places=2)

        # Verify file exists
        self.assertTrue(os.path.exists(output_path), f"CV file not found at {output_path}")

    @patch("src.tailoring._load_profile")
    def test_full_pipeline_state_transitions(self, mock_load_profile):
        """Full pipeline: discovered -> scored -> tailored state transitions."""
        import unittest.mock as _mock
        import yaml as _yaml

        sample_profile_dict = {
            "personal": {"name": "Test User", "email": "test@example.com",
                         "phone": "+1 555-0000", "location": "Remote"},
            "summary": "Python developer",
            "experience": [
                {
                    "title": "Senior Software Engineer",
                    "company": "TechCorp",
                    "start": "2021-01", "end": "present",
                    "bullets": ["Built Django APIs", "Led team"],
                    "skills": ["Python", "Django"],
                },
                {
                    "title": "Software Engineer",
                    "company": "WebCo",
                    "start": "2018-01", "end": "2020-12",
                    "bullets": ["Developed React app"],
                    "skills": ["React"],
                },
            ],
            "skills": {
                "languages": ["Python"],
                "frameworks": ["Django", "React"],
                "databases": ["PostgreSQL"],
                "cloud": ["AWS", "Docker"],
            },
            "certifications": [],
            "languages": [{"language": "English", "level": "Native", "cefr": "C2"}],
        }
        mock_load_profile.return_value = (_yaml.dump(sample_profile_dict), sample_profile_dict)

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        def _msg(text):
            return MagicMock(content=[MagicMock(text=text)])
        # Sequence: scoring | keyword extraction (Pass 1) | tailoring (Pass 2)
        mock_client.messages.create.side_effect = [
            _msg(MOCK_SCORE_RESPONSE),
            _msg(MOCK_KEYWORD_RESPONSE),
            _msg(MOCK_TAILORING_RESPONSE),
        ]

        # Verify initial state
        conn = get_connection(self.db_path)
        job_initial = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()
        self.assertEqual(job_initial["status"], "discovered")
        self.assertIsNone(job_initial["score"])

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            # Step 1: Score
            from src.scoring import run_scoring
            run_scoring(self.config, self.db_path, limit=10)

            conn = get_connection(self.db_path)
            job_after_score = get_job_by_id(conn, FAKE_JOB_ID)
            conn.close()
            self.assertEqual(job_after_score["status"], "scored")
            self.assertIsNotNone(job_after_score["score"])

            # Step 2: Tailor
            from src.tailoring import run_tailoring
            try:
                run_tailoring(self.config, self.db_path, FAKE_JOB_ID, format="american", lang="en")
            except ImportError:
                self.skipTest("python-docx not installed")

        conn = get_connection(self.db_path)
        job_final = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertEqual(job_final["status"], "tailored")
        self.assertIsNotNone(job_final["cv_path"])
        self.assertIsNotNone(job_final["ats_score"])
        self.assertGreater(float(job_final["ats_score"]), 0)


class TestCoverLetterIntegration(unittest.TestCase):
    """Integration test for cover letter generation."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        self.output_dir = tempfile.mkdtemp()
        init_db(self.db_path)
        conn = get_connection(self.db_path)
        scored_job = {
            **FAKE_JOB,
            "status": "tailored",
            "score": 7.8,
            "keywords_matched": json.dumps(["Python", "Django", "React"]),
            "cv_path": f"{self.output_dir}/{FAKE_JOB_ID}/cv_american.docx",
        }
        insert_job(conn, scored_job)
        conn.close()

        self.config = {
            "claude_api_key": "sk-test-key-for-mocking",
            "output": {"dir": self.output_dir},
            "database": {"path": self.db_path},
        }

    def tearDown(self):
        os.unlink(self.db_path)
        import shutil
        shutil.rmtree(self.output_dir, ignore_errors=True)

    MOCK_COVER_LETTER_TEXT = """
After six years of building full stack Python applications--most recently leading a Django/React platform serving 500k daily users at TechCorp--I was immediately drawn to the Full Stack Python Developer role at Innovate Inc.

At TechCorp, I architected a migration from a monolithic Django app to microservices on AWS ECS, reducing deployment time by 60%. I then led a React frontend overhaul that cut page load times by 35%, directly improving user retention. At WebAgency, I built a PostgreSQL-backed API layer that scaled to 200k requests/day with zero downtime during a critical product launch.

I'd welcome the chance to discuss how my background aligns with Innovate Inc's engineering roadmap. I'm available for a call any time this week.
""".strip()

    @patch("src.cover_letter._load_profile")
    def test_cover_letter_updates_db(self, mock_load_profile):
        """Cover letter generation should save the file and update the DB."""
        import unittest.mock as _mock

        mock_load_profile.return_value = {
            "personal": {
                "name": "Jane Developer",
                "email": "jane@example.com",
                "phone": "+1 555-1234",
                "location": "San Francisco, CA",
            },
            "summary": "Full stack Python developer",
            "skills": {"languages": ["Python"], "frameworks": ["Django", "React"]},
        }

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=self.MOCK_COVER_LETTER_TEXT)]
        )

        with _mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from src.cover_letter import run_cover_letter
            try:
                output_path = run_cover_letter(self.config, self.db_path, FAKE_JOB_ID)
            except ImportError:
                self.skipTest("python-docx not installed")

        self.assertTrue(os.path.exists(output_path))
        self.assertTrue(output_path.endswith(".docx"))

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, FAKE_JOB_ID)
        conn.close()

        self.assertIsNotNone(job["cover_letter_path"])
        self.assertIn("cover_letter", job["cover_letter_path"])


if __name__ == "__main__":
    unittest.main()
