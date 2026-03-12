The key idea behind adaptive profit targets is that the expected move depends on two things:

Trend strength (slope of the trend)

Current volatility

When both are high → trends travel much farther
When both are low → trends stall quickly

So instead of:

target = entry + 2 * ATR

you compute a dynamic target based on those conditions.

1. Measure Trend Strength (Slope)

Since you're using two Kaufman Adaptive Moving Average lines already, you have a very clean way to estimate slope.

A simple version:

trend_slope = fast_KAMA - fast_KAMA[5]

or normalized:

trend_slope = (fast_KAMA - fast_KAMA[5]) / ATR

Interpretation:

Slope	Meaning
< 0.2	weak trend
0.2 – 0.5	moderate
> 0.5	strong trend

This gives you a dimensionless trend strength score.

2. Measure Volatility

You already use Average True Range, which is perfect.

But volatility should be relative, not absolute.

volatility_ratio = ATR / ATR_20

Meaning:

Ratio	Market
< 0.8	quiet
0.8 – 1.2	normal
> 1.2	expanding
3. Adaptive Profit Target

Now combine both.

target_distance =
    ATR *
    (1 + trend_slope * 2) *
    volatility_ratio

Example scenarios:

Trend	Volatility	Target
weak	quiet	~1 ATR
moderate	normal	~2 ATR
strong	expanding	4–6 ATR

Suddenly the system naturally adapts to the day.

Example Walkthrough

Entry:

price = 100
ATR = 1.2
trend_slope = 0.6
volatility_ratio = 1.3

Target:

distance = 1.2 * (1 + 0.6*2) * 1.3
distance ≈ 3.4
profit target ≈ 103.4

But on a slow day:

trend_slope = 0.1
volatility_ratio = 0.7
distance ≈ 1.0
target ≈ 101

So the strategy automatically scalps small days and rides big days.

4. Even Better: Dynamic Trailing Target

Instead of a fixed target, update it during the trade.

Every bar:

expected_move =
    ATR *
    (1 + trend_slope * 2) *
    volatility_ratio

Exit when:

profit >= expected_move
AND price closes below fast_KAMA

This avoids exiting early during trend acceleration.

5. A Simpler Version (Highly Effective)

If you want something easier but very powerful:

trend_factor = abs(fast_KAMA - slow_KAMA) / ATR

target = entry + ATR * (1 + trend_factor * 3)

Meaning:

KAMA separation	Target
small	1–1.5 ATR
medium	2–3 ATR
large	4–5 ATR

Which aligns extremely well with how trends behave.