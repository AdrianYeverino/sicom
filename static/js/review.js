// Margin and retail price move together: change one, the other follows,
// so the person sees the public price of a margin before saving it.
// price = cost with tax × (1 + margin / 100)
document.addEventListener("alpine:init", function () {
  "use strict";
  function two(n) { return Math.round(n * 100) / 100; }

  Alpine.data("price", function (costWithTax, margin, price) {
    return {
      cost: costWithTax,
      margin: margin === null ? "" : String(margin),
      price: price === null ? "" : String(price),
      by: "price",
      fromMargin: function () {
        var m = parseFloat(this.margin);
        this.by = "margin";
        this.price = isNaN(m) ? "" : two(this.cost * (1 + m / 100)).toFixed(2);
      },
      fromPrice: function () {
        var p = parseFloat(String(this.price).replace(/[$,]/g, ""));
        this.by = "price";
        this.margin = isNaN(p) || !this.cost ? "" : two((p / this.cost - 1) * 100).toFixed(2);
      },
      setPrice: function (p) {
        this.price = Number(p).toFixed(2);
        this.fromPrice();
      },
      round: function (step) {
        var p = parseFloat(this.price);
        if (isNaN(p)) return;
        this.setPrice(Math.round(p / step) * step);
      },
    };
  });
});
