I want to build a trade manager for my personal trading platform project.  The job of the trade manager page is to provide the user an editing platform to setup a trade agent that can be displayed on our chart. 

The page is going to need some kind of design language or model that would allow the user to build complex trading strategies for testing.  

Such as "Enter trade when price closes below the 20 SMA, when the 20 SMA is > 50 SMA and the two SMAs crossed within the last 200 candles".  Maybe this looks like:

entry = if CLOSE < SMA-20:Value AND SMA-20:Value > SMA-50:Value AND ( SMA-20:Crossover == 1 WITHIN 200 BARS ) THEN True else False

We could build it in YAML, or JSON

We need to be able to Load save and edit trade plans. Once a trade plan is selected and the manager is closed the chart will come back up.  The trade plan needs to be given to a trade engine on our backend that will walk the entire chart from oldest to newest and mark entry's and exits appropriately.  The trade manager widget will display the results, currently holding mock values.

Whatever we pick it must be usable for phase 2
I'd like to implement an LLM that can translate your trade idea's into the trade language, offer suggestions, help identify patterns, etc.

Phase 0 MVP
- Load, Save, Edit strategy's
- A minimal DSL that supports
  - A > B
  - A < B
  - IN_BULL_TRADE & !IN_BULL_TRADE
  -- These will be used as quatifiers for if a second bull trade is allowed if we're already in one.
- A text field to edit out strategy
- A validate strategy syntax button
- An apply button to exit back to the chart
- A trade engine that will do our rule evaluations
  - It should walk the chart from left to right and put ENTRY and EXIT markers by the candles
  - We can work on stats in another phase for now lets just get a marker on the chart successfully

Ask any questions and then make yourself a detailed TODO.md