const test = require('node:test');
const assert = require('node:assert');
const { Cart, _round_cents } = require('./pricing');

test('test_round_half_up', () => {
  assert.strictEqual(_round_cents(0.125), 0.13);
  assert.strictEqual(_round_cents(2.345), 2.35);
});

test('test_line_total_rounds', () => {
  const c = new Cart().add_item("x", "0.125", 1);
  assert.strictEqual(c.subtotal(), 0.13);
});

test('test_none_tier_subtotal', () => {
  const c = new Cart().add_item("x", "100.00", 1);
  assert.strictEqual(c.discounted_subtotal(), 100.00);
});

test('test_silver_tier', () => {
  const c = new Cart("silver").add_item("x", "100.00", 1);
  assert.strictEqual(c.discounted_subtotal(), 95.00);
});

test('test_gold_tier', () => {
  const c = new Cart("gold").add_item("x", "100.00", 1);
  assert.strictEqual(c.discounted_subtotal(), 90.00);
});

test('test_percentage_applied_before_fixed_coupon', () => {
  const c = new Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00");
  assert.strictEqual(c.discounted_subtotal(), 80.00);
});

test('test_fixed_coupon_clamped_to_zero', () => {
  const c = new Cart().add_item("x", "30.00", 1).add_coupon("50.00");
  assert.strictEqual(c.discounted_subtotal(), 0.00);
});

test('test_total_never_negative', () => {
  const c = new Cart().add_item("x", "30.00", 1).add_coupon("50.00");
  assert.strictEqual(c.total(), 0.00);
});

test('test_free_shipping_at_or_above_threshold', () => {
  const c = new Cart().add_item("x", "60.00", 1);
  assert.strictEqual(c.shipping(), 0.00);
});

test('test_flat_shipping_below_threshold', () => {
  const c = new Cart().add_item("x", "40.00", 1);
  assert.strictEqual(c.shipping(), 5.99);
});

test('test_zero_subtotal_waives_shipping_and_tax', () => {
  const c = new Cart().add_item("x", "30.00", 1).add_coupon("30.00");
  assert.strictEqual(c.shipping(), 0.00);
  assert.strictEqual(c.tax(), 0.00);
  assert.strictEqual(c.total(), 0.00);
});

test('test_tax_on_discounted_excludes_shipping', () => {
  const c = new Cart().add_item("x", "40.00", 1);
  assert.strictEqual(c.tax(), 3.50);
  assert.strictEqual(c.total(), 49.49);
});

test('test_full_total_with_tier', () => {
  const c = new Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00");
  assert.strictEqual(c.total(), 87.00);
});

test('test_multi_item_subtotal', () => {
  const c = new Cart().add_item("a", "10.00", 2).add_item("b", "3.33", 3);
  assert.strictEqual(c.subtotal(), 29.99);
});
