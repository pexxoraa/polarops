import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.inventory_service import resolve_inventory_adjustment


class InventoryAdjustmentTests(unittest.TestCase):
    def test_resupply_adds_positive_amount(self):
        delta, quantity, reason = resolve_inventory_adjustment(10, 3, "Resupply")
        self.assertEqual((delta, quantity, reason), (3, 13, "Resupply"))

    def test_consumption_subtracts_positive_amount(self):
        delta, quantity, reason = resolve_inventory_adjustment(10, 3, "Field consumption")
        self.assertEqual((delta, quantity, reason), (-3, 7, "Field consumption"))

    def test_damaged_or_lost_subtracts_positive_amount(self):
        delta, quantity, reason = resolve_inventory_adjustment(10, 2, "Damaged / lost")
        self.assertEqual((delta, quantity, reason), (-2, 8, "Damaged / lost"))

    def test_transfer_correction_preserves_sign(self):
        self.assertEqual(resolve_inventory_adjustment(10, -2, "Transfer correction")[:2], (-2, 8))
        self.assertEqual(resolve_inventory_adjustment(10, 2, "Transfer correction")[:2], (2, 12))

    def test_stock_count_correction_sets_counted_quantity(self):
        delta, quantity, reason = resolve_inventory_adjustment(10, 7, "Stock count correction")
        self.assertEqual((delta, quantity, reason), (-3, 7, "Stock count correction"))

    def test_adjustment_cannot_make_inventory_negative(self):
        with self.assertRaisesRegex(ValueError, "Inventory cannot be negative"):
            resolve_inventory_adjustment(2, 3, "Field consumption")

    def test_unknown_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid inventory adjustment reason"):
            resolve_inventory_adjustment(10, 1, "Unknown")


if __name__ == "__main__":
    unittest.main()
