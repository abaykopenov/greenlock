// bench_pricing.pricing — движок ценообразования корзины.

const TAX_RATE = 0.0875;
const FREE_SHIP_THRESHOLD = 50.00;
const SHIP_FLAT = 5.99;
const LOYALTY_RATES = {
  "none": 0.00,
  "silver": 0.05,
  "gold": 0.10
};

function _round_cents(amount) {
  // Денежное округление half-up до центов
  return Math.round((Number(amount) + Number.EPSILON) * 100) / 100;
}

class LineItem {
  constructor(name, unit_price, qty = 1) {
    this.name = name;
    this.unit_price = Number(unit_price);
    this.qty = Number(qty);
  }

  get line_total() {
    return _round_cents(this.unit_price * this.qty);
  }
}

class Cart {
  constructor(loyalty = "none") {
    this._items = [];
    this._coupons = [];
    this.loyalty = loyalty;
  }

  add_item(name, unit_price, qty = 1) {
    this._items.push(new LineItem(name, unit_price, qty));
    return this;
  }

  add_coupon(amount) {
    this._coupons.push(Number(amount));
    return this;
  }

  _percentage_rate() {
    return LOYALTY_RATES[this.loyalty] || 0.00;
  }

  subtotal() {
    const sum = this._items.reduce((acc, it) => acc + it.line_total, 0);
    return _round_cents(sum);
  }

  discounted_subtotal() {
    const sub = this.subtotal();
    const after_pct = _round_cents(sub * (1 - this._percentage_rate()));
    const coupon_sum = this._coupons.reduce((acc, c) => acc + c, 0);
    let after_fixed = after_pct - coupon_sum;
    if (after_fixed < 0) {
      after_fixed = 0.00;
    }
    return _round_cents(after_fixed);
  }

  shipping() {
    const ds = this.discounted_subtotal();
    if (ds === 0) {
      return 0.00;
    }
    return ds >= FREE_SHIP_THRESHOLD ? 0.00 : SHIP_FLAT;
  }

  tax() {
    const ds = this.discounted_subtotal();
    if (ds === 0) {
      return 0.00;
    }
    return _round_cents(ds * TAX_RATE);
  }

  total() {
    let t = this.discounted_subtotal() + this.shipping() + this.tax();
    if (t < 0) {
      t = 0.00;
    }
    return _round_cents(t);
  }
}

module.exports = {
  TAX_RATE,
  FREE_SHIP_THRESHOLD,
  SHIP_FLAT,
  LOYALTY_RATES,
  _round_cents,
  LineItem,
  Cart
};
