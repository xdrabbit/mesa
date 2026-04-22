# Mesa Screening Strategy

**Version 2: Quality-Focused Wheel Strategy**

## Philosophy

We're not hunting for premium anywhere. We're looking for **quality companies we'd genuinely want to own** if assigned shares, with acceptable premium levels.

This is a conservative approach: better to miss opportunities than to assign yourself 100 shares of a volatile meme stock.

## Watchlist Hierarchy

### Priority Watchlist (Screen First)
These are quality mega-cap + established growth companies:
```
JPM, ABBV, KO, DDOG, MSFT, AAPL, AMZN,
GOOGL, META, V, MA, UNH, PG, JNJ,
WMT, HD, NET, SNOW, CRM, NOW
```

**Why these?**
- Strong balance sheets
- Dividend history or consistent profitability
- Market cap $10B+ (institutional ownership)
- Price range sweet spot ($50-$110)
- Wheel strategy candidates (boring but profitable)

### Fallback Watchlist (Expand if Needed)
If priority watchlist has no opportunities:
```
PLTR, AMD, UBER, NFLX, ABNB, SHOP,
PINS, DASH, ROKU, SMCI
```

## Exclusion Filters

### 1. Crypto-Related Stocks (BANNED)
Too volatile, wrong risk profile for wheel strategy.
```
MARA, RIOT, COIN, MSTR, CLSK, HOOD
```
Crypto miners = unpredictable leverage to BTC price.  
Crypto trackers = useless if you get assigned.

### 2. Price Range Filter
- **Minimum:** $50/share (too small = wrong volatility profile)
- **Maximum:** $110/share (capital constraint: $10k per contract)

Why $50 min? Stocks under $50 often have:
- Smaller market cap (less stable)
- Higher relative volatility
- Smaller bid-ask spreads (misleading premiums)

### 3. Market Cap Filter
- **Minimum:** $10 billion
- Why? Institutional ownership = better options liquidity, fewer micro-cap shenanigans

### 4. Accounting/SEC Issues
If a company is under investigation or has restatement risk, skip it.
- Check SEC filings before screening
- Update exclusion list if needed

### 5. "Would You Own This?" Test
Before taking a put assignment, ask:
- Would I hold 100 shares at this strike?
- For how long?
- What's my exit plan?

If the answer is "no" or "I don't know," skip it.

## Screening Criteria

### Required
- OTM puts (strike < current price)
- Moneyness 80-95% (delta roughly -0.20 to -0.35)
- DTE 30-60 days
- Annualized return ≥ 15%

### Liquidity Thresholds
- Open Interest ≥ 100 contracts
- Bid-Ask Spread ≤ 20%

### Output Metrics
For each opportunity:
- **Ticker** — Stock symbol
- **Price** — Current close
- **Strike** — Put strike (assignment price)
- **Expiry** — Option expiration date
- **DTE** — Days to expiration
- **Premium** — Contract mid-price
- **Annualized** — Return on capital (%)
- **Breakeven** — Downside protection
- **Cushion** — Margin below current price
- **OI** — Open interest
- **Spread** — Bid-ask spread %

## Screening Flow

### Step 1: Parse Intent
User sends: "find quality puts under $60"
Bot parses: price_max=60, limit=3

### Step 2: Screen Priority Watchlist
Loop through: JPM, ABBV, KO, DDOG, ... (19 tickers)

For each ticker:
1. Check exclusion filters (crypto? accounting issues?)
2. Fetch current price
3. Check price range ($50-$110)
4. Check market cap ($10B+)
5. If passes, scan options for hits

### Step 3: Filter Options
For each expiration:
- Check DTE (30-60 days)
- Check OTM puts
- Check moneyness (80-95%)
- Check liquidity (OI ≥ 100, spread ≤ 20%)
- Calculate annualized return

### Step 4: Rank & Return
Sort by annualized return (descending)
Return top 3 (or user-specified limit)

### Step 5: Expand if Needed
If priority watchlist has no hits:
- Repeat with fallback watchlist
- Stop before going too broad

## Examples

### Example 1: "Find quality puts under $80"
```
✓ Scan: JPM, ABBV, KO, DDOG, MSFT, ... (priority)
✓ Filter: Current price $50-$80
✓ Check: Market cap ≥ $10B
✓ Screen: Options for 30-60 DTE puts
✓ Result: Top 3 by annualized return
```

### Example 2: "High IV opportunities"
Bot currently doesn't estimate IV, so it:
1. Screens priority watchlist
2. Returns candidates with highest premium %
3. (Future: add IV rank estimation)

### Example 3: "Check JPM"
```
✓ User specified JPM
✓ Skip watchlist logic, go straight to JPM
✓ Screen all active expirations
✓ Return best hit on JPM
```

## Why This Approach

### Conservative
- Only quality companies
- No micro-caps or crypto
- Only companies with $10B+ market cap

### Sustainable
- If assigned, you own something decent
- Can hold or sell covered calls
- Wheel repeats smoothly

### Focused
- Priority watchlist = 19 tickers (fast scan)
- No FOMO on random penny stocks
- Less is more

## Maintenance

### Update Crypto Exclusion
```python
CRYPTO_EXCLUSION = {"MARA", "RIOT", "COIN", "MSTR", "CLSK", "HOOD"}
```
Add/remove as needed.

### Add Accounting Issues
```python
ACCOUNTING_ISSUES = {"XXX"}  # Add ticker if SEC issue surfaces
```

### Adjust Thresholds
- `MIN_STOCK_PRICE` — lower if you want smaller companies
- `MIN_MARKET_CAP` — raise to $20B+ for mega-caps only
- `MIN_ANNUALIZED_RETURN` — lower if you want more options

## Future Enhancements

- [ ] IV Rank estimation (Black-Scholes)
- [ ] Earnings calendar (avoid earnings weeks)
- [ ] Dividend tracking (ex-div dates)
- [ ] Custom watchlists per user
- [ ] Assignment alert system
- [ ] Portfolio-aware screening (avoid overconcentration)

## References

- Wheel Strategy: Sell puts → Get assigned → Sell calls
- OTM: Out-of-the-money (strike < current price)
- Moneyness: Strike / Current Price (closer to 1.0 = more ITM)
- Breakeven: Strike - Premium (how far stock can drop)
- Cushion: (Current - Breakeven) / Current × 100%

---

**Last updated:** April 17, 2026  
**Next review:** Monthly or after major market event
