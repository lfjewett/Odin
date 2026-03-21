# An RSS Feed for reuters from google
https://news.google.com/rss/search?q=site%3Areuters.com&hl=en-US&gl=US&ceid=US%3Aen
  - Description here: https://www.reddit.com/r/rss/comments/1dpaakn/reuters_rss/

# Some paid API's
 - https://newsapi.org/docs/get-started#top-headlines
 - https://stocknewsapi.com/?gad_source=1&gad_campaignid=21185490165&gbraid=0AAAAAqEF1t96S32HBAII-DXeezlLbBMKd&gclid=Cj0KCQiA5I_NBhDVARIsAOrqIsaoahfi3FWFnEFXhRvzivJBYj7Xk1jtc_Juh4oQbmD9xl_OawaADb0aAiueEALw_wcB
 - https://www.marketaux.com/pricing
 - https://newsdata.io/pricing
 - https://finlight.me/pricing
 
 To check if a stock symbol has recent news, I'm currently using the TradingView headlines endpoint below:

url = (

"https://news-headlines.tradingview.com/headlines/"

"?category=stock"

"&lang=en"

f"&symbol={symbol_param}"

)