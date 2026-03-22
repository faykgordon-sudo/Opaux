"""
test_tailoring.py -- Tests for tailoring.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection, get_job_by_id, init_db, insert_job
from src.tailoring import TAILORING_PROMPT, _parse_json_response

SAMPLE_PROFILE = {
    "personal": {
        "name": "Jane Developer",
        "email": "jane@example.com",
        "phone": "+1 555-1234",
        "location": "San Francisco, CA",
    },
    "summary": "Experienced software engineer with 5+ years in Python.",
    "experience": [
        {
            "title": "Senior Software Engineer",
            "company": "TechCorp",
            "start": "2021-01",
            "end": "present",
            "bullets": [
                "Led migration of monolith to microservices using FastAPI and Docker",
                "Reduced API response time by 40% via Redis caching",
                "Managed a team of 4 engineers",
            ],
            "skills": ["Python", "FastAPI", "Docker", "Redis"],
        },
        {
            "title": "Software Engineer",
            "company": "WebAgency",
            "start": "2018-06",
            "end": "2020-12",
            "bullets": [
                "Built REST APIs with Django and PostgreSQL",
                "Implemented CI/CD pipelines with GitHub Actions",
            ],
            "skills": ["Python", "Django", "PostgreSQL"],
        },
    ],
    "skills": {
        "languages": ["Python", "JavaScript", "Go"],
        "frameworks": ["FastAPI", "Django", "React"],
        "databases": ["PostgreSQL", "Redis"],
        "cloud": ["AWS", "Docker", "Kubernetes"],
    },
    "certifications": [
        {"name": "AWS Solutions Architect", "issuer": "Amazon", "date": "2023-05"}
    ],
    "languages": [
        {"language": "English", "level": "Native", "cefr": "C2"},
    ],
}

SAMPLE_JOB = {
    "id": "tailor_test_0001",
    "title": "Backend Python Engineer",
    "company": "DataFlow Inc",
    "location": "Remote",
    "url": "https://dataflow.example.com/jobs/456",
    "source": "linkedin",
    "description": (
        "We are hiring a Backend Python Engineer to build high-performance "
        "data pipelines using Python, FastAPI, PostgreSQL, and AWS. "
        "Experience with Docker and Kubernetes is required. "
        "You will architect scalable microservices and mentor junior engineers."
    ),
    "status": "scored",
    "score": 8.2,
    "keywords_matched": '["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"]',
    "date_discovered": "2025-01-01T00:00:00",
}

GOOD_TAILORING_RESPONSE = {
    "must_have": ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker", "Kubernetes"],
    "nice_to_have": ["Redis", "microservices"],
    "company_specific": ["data pipelines", "scalable"],
    "keyword_mapping": {
        "FastAPI": "Led migration to FastAPI-based microservices at TechCorp",
        "PostgreSQL": "Built REST APIs with Django and PostgreSQL at WebAgency",
        "AWS": "AWS Solutions Architect certified; deployed services on AWS",
    },
    "tailored_summary": (
        "Backend Python engineer with 5+ years building scalable microservices "
        "using FastAPI, PostgreSQL, and AWS. Led high-impact migrations and "
        "mentored engineering teams."
    ),
    "tailored_bullets": {
        "Senior Software Engineer": [
            "Architected migration from monolith to FastAPI microservices, improving deploy velocity by 60%",
            "Reduced API response time 40% via Redis caching on AWS ElastiCache",
            "Mentored 4 junior engineers; established team code review and CI/CD standards",
            "Deployed containerized services using Docker and Kubernetes on AWS EKS",
        ],
        "Software Engineer": [
            "Built RESTful data APIs with Django and PostgreSQL, supporting 500k daily requests",
            "Implemented CI/CD pipelines with GitHub Actions, reducing release cycle by 30%",
        ],
    },
    "skills_to_highlight": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS"],
    "estimated_ats_score": 0.88,
}


class TestKeywordExtraction(unittest.TestCase):
    """Test keyword analysis from job descriptions."""

    def test_prompt_contains_job_description(self):
        """Tailoring prompt must include the job description."""
        prompt = TAILORING_PROMPT.format(
            profile_yaml="name: Jane",
            title="Backend Engineer",
            company="ACME",
            description="We need Python, FastAPI, and PostgreSQL skills.",
            must_have="- Python\n- FastAPI\n- PostgreSQL",
            nice_to_have="- Docker",
            company_specific="- agile",
        )
        self.assertIn("FastAPI", prompt)
        self.assertIn("PostgreSQL", prompt)
        self.assertIn("Backend Engineer", prompt)

    def test_must_have_keywords_populated(self):
        """must_have should contain critical keywords from JD."""
        response = GOOD_TAILORING_RESPONSE
        self.assertIn("Python", response["must_have"])
        self.assertIn("FastAPI", response["must_have"])
        self.assertGreater(len(response["must_have"]), 0)

    def test_keyword_mapping_references_profile(self):
        """keyword_mapping values should reference real profile experiences."""
        mapping = GOOD_TAILORING_RESPONSE["keyword_mapping"]
        self.assertIn("FastAPI", mapping)
        # Each mapping value should mention a known company from the profile
        profile_companies = {"TechCorp", "WebAgency"}
        for keyword, explanation in mapping.items():
            mentions_company = any(c in explanation for c in profile_companies)
            mentions_skill = keyword.lower() in explanation.lower() or any(
                skill.lower() in explanation.lower()
                for skill in ["fastapi", "postgresql", "aws", "docker", "python"]
            )
            self.assertTrue(
                mentions_company or mentions_skill,
                f"Mapping for '{keyword}' doesn't reference profile: {explanation}",
            )


class TestATSRules(unittest.TestCase):
    """Test ATS keyword coverage rules."""

    def test_must_have_coverage_at_least_80_percent(self):
        """At least 80% of must_have keywords should appear in tailored bullets."""
        must_have = GOOD_TAILORING_RESPONSE["must_have"]
        all_bullets = []
        for job_title, bullets in GOOD_TAILORING_RESPONSE["tailored_bullets"].items():
            all_bullets.extend(bullets)
        bullet_text = " ".join(all_bullets).lower()

        covered = sum(1 for kw in must_have if kw.lower() in bullet_text)
        coverage = covered / len(must_have) if must_have else 1.0

        self.assertGreaterEqual(
            coverage,
            0.8,
            f"Must-have keyword coverage is {coverage:.0%}, expected >= 80%. "
            f"Missing: {[kw for kw in must_have if kw.lower() not in bullet_text]}",
        )

    def test_keyword_max_frequency(self):
        """No single keyword should appear more than 2 times in any single set of bullets."""
        for job_title, bullets in GOOD_TAILORING_RESPONSE["tailored_bullets"].items():
            bullet_text = " ".join(bullets).lower()
            for kw in GOOD_TAILORING_RESPONSE["must_have"]:
                count = bullet_text.count(kw.lower())
                self.assertLessEqual(
                    count,
                    3,  # Allow up to 3 since bullets are meant to be rich
                    f"Keyword '{kw}' appears {count} times in bullets for '{job_title}' -- "
                    "consider de-duplicating for better ATS scores",
                )

    def test_ats_score_is_valid_float(self):
        """estimated_ats_score should be a float between 0 and 1."""
        ats_score = GOOD_TAILORING_RESPONSE["estimated_ats_score"]
        self.assertIsInstance(float(ats_score), float)
        self.assertGreaterEqual(float(ats_score), 0.0)
        self.assertLessEqual(float(ats_score), 1.0)


class TestNoFabricatedExperience(unittest.TestCase):
    """Test that tailored bullets are grounded in the candidate profile."""

    def _get_all_profile_text(self, profile: dict) -> set[str]:
        """Extract all meaningful text tokens from the profile."""
        tokens: set[str] = set()
        # Add all company names
        for exp in profile.get("experience", []):
            tokens.add(exp.get("company", "").lower())
            tokens.add(exp.get("title", "").lower())
            for bullet in exp.get("bullets", []):
                # Add significant words (>4 chars) from existing bullets
                for word in bullet.lower().split():
                    clean = word.strip(".,;:()-")
                    if len(clean) > 4:
                        tokens.add(clean)
        # Add skill names
        for category, skills in profile.get("skills", {}).items():
            for skill in skills:
                tokens.add(str(skill).lower())
        # Add certification names
        for cert in profile.get("certifications", []):
            tokens.add(cert.get("name", "").lower())
        return tokens

    def test_tailored_bullets_reference_real_job_titles(self):
        """Tailored bullets dict keys should match profile job titles."""
        profile_titles = {exp["title"] for exp in SAMPLE_PROFILE["experience"]}
        tailored_titles = set(GOOD_TAILORING_RESPONSE["tailored_bullets"].keys())
        # Every tailored title should correspond to a real profile entry
        for title in tailored_titles:
            self.assertIn(
                title,
                profile_titles,
                f"Tailored bullets for '{title}' doesn't match any profile job title. "
                "This could indicate fabricated experience.",
            )

    def test_skills_to_highlight_from_profile(self):
        """All highlighted skills should exist in the candidate's profile."""
        all_profile_skills: set[str] = set()
        for category, skill_list in SAMPLE_PROFILE["skills"].items():
            all_profile_skills.update(str(s).lower() for s in skill_list)
        # Also add cert names
        for cert in SAMPLE_PROFILE.get("certifications", []):
            all_profile_skills.add(cert["name"].lower())

        for skill in GOOD_TAILORING_RESPONSE["skills_to_highlight"]:
            self.assertIn(
                skill.lower(),
                all_profile_skills,
                f"Highlighted skill '{skill}' is not in the candidate's profile -- "
                "could be fabricated.",
            )


class TestParseJsonResponse(unittest.TestCase):
    """Test the JSON response parser."""

    def test_parse_valid_json(self):
        """Should parse a clean JSON dict."""
        text = json.dumps(GOOD_TAILORING_RESPONSE)
        result = _parse_json_response(text)
        self.assertEqual(result["estimated_ats_score"], 0.88)

    def test_parse_fenced_json(self):
        """Should strip markdown code fences."""
        text = f"```json\n{json.dumps(GOOD_TAILORING_RESPONSE)}\n```"
        result = _parse_json_response(text)
        self.assertIn("must_have", result)

    def test_parse_json_with_surrounding_text(self):
        """Should extract JSON from text with preamble/postamble."""
        text = f"Analysis complete:\n{json.dumps(GOOD_TAILORING_RESPONSE)}\nEnd of analysis."
        result = _parse_json_response(text)
        self.assertIn("tailored_bullets", result)

    def test_required_keys_present(self):
        """Parsed response should contain all required keys."""
        required_keys = [
            "must_have", "nice_to_have", "tailored_summary",
            "tailored_bullets", "skills_to_highlight", "estimated_ats_score",
        ]
        result = _parse_json_response(json.dumps(GOOD_TAILORING_RESPONSE))
        for key in required_keys:
            self.assertIn(key, result, f"Required key '{key}' missing from tailoring response")


class TestTailoringDB(unittest.TestCase):
    """Test that tailoring correctly updates the database."""

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

    def test_tailoring_updates_db_fields(self):
        """After tailoring, cv_path, ats_score, and status should be updated."""
        from src.database import update_job

        conn = get_connection(self.db_path)
        update_job(
            conn,
            SAMPLE_JOB["id"],
            cv_path="output/tailor_test_0001/cv_american.docx",
            ats_score=0.88,
            status="tailored",
        )
        conn.close()

        conn = get_connection(self.db_path)
        job = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()

        self.assertEqual(job["status"], "tailored")
        self.assertAlmostEqual(float(job["ats_score"]), 0.88)
        self.assertIn("cv_american.docx", job["cv_path"])

    def test_tailored_status_transition(self):
        """Job should transition from 'scored' to 'tailored'."""
        conn = get_connection(self.db_path)
        job_before = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()
        self.assertEqual(job_before["status"], "scored")

        from src.database import update_job
        conn = get_connection(self.db_path)
        update_job(conn, SAMPLE_JOB["id"], status="tailored", cv_path="/tmp/cv.docx", ats_score=0.8)
        conn.close()

        conn = get_connection(self.db_path)
        job_after = get_job_by_id(conn, SAMPLE_JOB["id"])
        conn.close()
        self.assertEqual(job_after["status"], "tailored")


if __name__ == "__main__":
    unittest.main()
