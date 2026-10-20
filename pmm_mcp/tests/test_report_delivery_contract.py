import unittest

from pmm_mcp.server import generate_performance_reports


class ReportDeliveryContractTests(unittest.TestCase):
    def test_generator_requires_google_docs_handoff_for_complete_delivery(self) -> None:
        description = generate_performance_reports.__doc__ or ""
        self.assertIn("Google Docs novo", description)
        self.assertIn("modelo visual aprovado", description)
        self.assertIn("não entregue somente este resultado estruturado", description)


if __name__ == "__main__":
    unittest.main()
