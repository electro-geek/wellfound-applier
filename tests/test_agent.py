import json
import unittest

import wellfound_agent as agent
import career_scanner


class AgentTests(unittest.TestCase):
    def test_backend_role_scores_high(self):
        profile = json.loads(agent.PROFILE_PATH.read_text())
        score, reason = agent.inferred_score({
            "title": "Backend Engineer",
            "location": "Remote India",
            "description": "Build Python FastAPI services using PostgreSQL, Docker and Kubernetes",
        }, profile)
        self.assertGreaterEqual(score, 65)
        self.assertIn("Python", reason)

    def test_frontend_role_is_excluded(self):
        profile = json.loads(agent.PROFILE_PATH.read_text())
        score, reason = agent.inferred_score({
            "title": "Frontend Engineer",
            "location": "Remote",
            "description": "React application",
        }, profile)
        self.assertEqual(score, 0)
        self.assertIn("Excluded", reason)

    def test_senior_experience_requirement_is_rejected(self):
        profile = json.loads(agent.PROFILE_PATH.read_text())
        score, reason = agent.inferred_score({
            "title": "Backend Engineer",
            "location": "India",
            "description": "Requires 8+ years of Python experience",
        }, profile)
        self.assertLess(score, 65)
        self.assertIn("8 years", reason)

    def test_bing_redirect_is_decoded(self):
        encoded = "a1aHR0cHM6Ly9qb2JzLmFzaGJ5aHEuY29tL2V4YW1wbGU="
        url = f"https://www.bing.com/ck/a?u={encoded}"
        self.assertEqual(career_scanner.decode_bing_url(url), "https://jobs.ashbyhq.com/example")

    def test_job_link_detection(self):
        self.assertTrue(career_scanner.is_jobish("Backend Engineer (Python)", "https://example.com/jobs/1"))
        self.assertFalse(career_scanner.is_jobish("Account Executive", "https://example.com/jobs/2"))

    def test_us_only_role_is_eligible(self):
        detail = {
            "finalUrl": "https://example.com/careers/united-states/backend-engineer",
            "body": "Backend Engineer\nUNITED STATES\nAbout the position",
        }
        self.assertTrue(career_scanner.eligible_job_location(detail))

    def test_india_role_is_eligible(self):
        detail = {"finalUrl": "https://example.com/jobs/1", "body": "Remote — India\nPython Engineer"}
        self.assertTrue(career_scanner.eligible_job_location(detail))

    def test_any_location_profile_accepts_foreign_role(self):
        detail = {"finalUrl": "https://example.com/jobs/1", "body": "Canada only"}
        self.assertTrue(career_scanner.eligible_job_location(detail, {"accept_any_location": True}))


if __name__ == "__main__":
    unittest.main()
