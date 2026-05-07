from SmartApi import SmartConnect
from pyotp import TOTP
# from IIFLapis import IIFLClient
import urllib
from datetime import datetime,date
import pandas as pd
# import chandu_util
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

class hist_data:
    def __init__(self):
        self.angel_obj = None
        self.IIFL_obj = None
        self.IIFL_script_master =None
        self.angle_script_master =None
        self.IIFL_client_id =None
        self.angel_WS_Obj = None

    def log_in(self):
        # load keys here and login
        angel_secret = open("keys/angleonekeys","r").read().split()
        # print("angel_secret {}".format(angel_secret))
        self.angel_obj = SmartConnect(api_key=angel_secret[0])
        # print("self.angel_obj {}".format(self.angel_obj))
        data = self.angel_obj.generateSession(angel_secret[2],angel_secret[3],TOTP(angel_secret[4]).now())
        # print("data {}".format(data))
        angel_WS_tocken = self.angel_obj.getfeedToken()
        # print("angel_WS_tocken {}".format(angel_WS_tocken))
        self.angel_WS_Obj  = SmartWebSocketV2(data["data"]["jwtToken"], angel_secret[0], angel_secret[2], angel_WS_tocken)
        # print("self.angel_WS_Obj  {}".format(self.angel_WS_Obj ))
        instrument_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        self.angle_script_master = pd.read_json(instrument_url)
        # print (self.angle_script_master)

        # IIFL_secret = open("keys/IIFLkeys","r").read().split()
        # self.IIFL_obj = IIFLClient(IIFL_secret[0], IIFL_secret[1], IIFL_secret[2], IIFL_secret[3],IIFL_secret[4])
        # self.IIFL_obj.client_login() #For Customer Login
        # self.IIFL_obj.jwt_validation(IIFL_secret[0])
        # self.IIFL_script_master = self.IIFL_obj.ScripMaster
        # self.IIFL_client_id =IIFL_secret[0]
        # print (self.IIFL_script_master)

    def get_eq_data(self, script_name, script_code, from_date, to_date, interval):
        params = {
            "exchange" : "NSE",
            "symboltoken":script_code,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
            }        
        hist_data = self.angel_obj.getCandleData(params)
        # print(hist_data)
        hist_df = self.angle_parsedata(hist_data["data"])
        # print(hist_df)
        return hist_df
    
    def get_iifl_data(self, script_name, script_code, from_date, to_date, interval):
        from_date= datetime.strptime(from_date, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d")
        # print ("Converted from date = {}".format(from_date))
        to_date= (datetime.strptime(to_date, "%Y-%m-%d %H:%M")).strftime("%Y-%m-%d")
        # print ("Converted to_date  = {}".format(to_date))
        match interval:
            case "ONE_MINUTE":
                interval ="1m"
            case "FIVE_MINUTE":
                interval ="5m"
            case "TEN_MINUTE":
                interval ="10m"
            case "FIFTEEN_MINUTE":
                interval ="15m"
            case "THIRTY_MINUTE":
                interval ="30m"
            case "ONE_HOUR":
                interval ="60m"
            case "ONE_DAY":
                interval ="1d"


        data =self.IIFL_obj.historical_candles(exch='n',exchType='c',scripcode=script_code,interval=interval,fromdate=from_date,todate=to_date,client_id=self.IIFL_client_id)
        hist_df =self.angle_parsedata(data["data"]["candles"])
        # print(hist_df)

    def angle_parsedata(self,data):
        # print((data))
        s_dates     =[]
        s_open      =[]
        s_high      =[]
        s_low       =[]
        s_close     =[]
        s_volume    =[]
        
        for candle in data:
            # print(candle)
            s_dates.append(candle[0].split("+")[0].replace("T", " "))
            s_open.append(candle[1])
            s_high.append(candle[2])
            s_low.append(candle[3])
            s_close.append(candle[4])
            s_volume.append(candle[5])

        # print(dates)
        df = pd.DataFrame(list(zip(s_dates,s_open, s_high,s_low,s_close,s_volume)), columns =['Date','Open', 'High', "Low","Close","Volume"])
        return df

    def token_lookup(self, ticker):
        eq_ticker = ticker+"-EQ"
        result = self.angle_script_master.loc[(self.angle_script_master["name"] == ticker) & \
                    (self.angle_script_master["exch_seg"] == "NSE") &\
                    (self.angle_script_master["symbol"] == eq_ticker) , "token"].iloc[0]
        return result
    
    def token_lookup_options(self, ticker):
        result = self.angle_script_master.loc[(self.angle_script_master["symbol"] == ticker) , "token"].iloc[0]
        return result

    def get_FNO_data(self, script_name, script_code, from_date, to_date, interval):
        params = {
            "exchange" : "NFO",
            "symboltoken":script_code,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
            }        
        hist_data = self.angel_obj.getCandleData(params)
        # print(hist_data)
        hist_df = self.angle_parsedata(hist_data["data"])
        # print(hist_df)
        return hist_df

    def place_limit_order(self,ticker,buy_sell,price,quantity,exchange="NSE"):
        params = {
                    "variety":"NORMAL",
                    "tradingsymbol":"{}-EQ".format(ticker),
                    "symboltoken":self.token_lookup(ticker),
                    "transactiontype":buy_sell,
                    "exchange":exchange,
                    "ordertype":"LIMIT",
                    "producttype":"INTRADAY",
                    "duration":"DAY",
                    "price":price,
                    "quantity":quantity
                    }
        response = self.angel_obj.placeOrder(params)
        return response

##01-Oct-2023

    def get_market_data(self, script_code, exchange = "NFO", mode = "FULL"):
        # params =  {
        #     "mode": mode,
        #     "exchangeTokens": {
        #         "NFO": [script_code]
        #     }
        #     }
        exchangeTokens = {
                exchange: [script_code]
            }
        # hist_data = self.angel_obj.chandu_getMarketData(params)
        hist_data = self.angel_obj.getMarketData(mode,exchangeTokens)
        # print(hist_data)
        hist_df = self.angle_parse_marketdata(hist_data["data"]["fetched"])
        # print(hist_df)
        return hist_df

    # Format is copied from get_market_data to get LTP alone
    def get_ltp_data(self, script_code, exchange = "NFO", mode = "FULL"):
        exchangeTokens = {
                exchange: [script_code]
            }
        hist_data = self.angel_obj.getMarketData(mode,exchangeTokens)
        # print(hist_data)
        ltp = hist_data["data"]["fetched"][0]["ltp"]
        # print(hist_df)
        return ltp


    def angle_parse_marketdata(self,data):
        # print((data))
        s_dates     =[]
        s_open      =[]
        s_high      =[]
        s_low       =[]
        s_close     =[]
        s_volume    =[]
        
        for candle in data:
            # print(candle)
            open_interest = candle["opnInterest"]
            # print(open_interest)
            return open_interest
            # s_dates.append(candle[0].split("+")[0].replace("T", " "))
            # s_open.append(candle[1])
            # s_high.append(candle[2])
            # s_low.append(candle[3])
            # s_close.append(candle[4])
            # s_volume.append(candle[5])

        # print(dates)
        # df = pd.DataFrame(list(zip(s_dates,s_open, s_high,s_low,s_close,s_volume)), columns =['Date','Open', 'High', "Low","Close","Volume"])
        # return df

# from quant_model import LSTM_model
# LSTM_model_obj = LSTM_model()
# # 05-Apr-2023
#     def store_daily_data(self):
#         data = hist_data()
#         data.log_in()
#         import time
#         from dateutil.relativedelta import relativedelta

#         FNO_LST_DF = chandu_util.get_FNO_LST_DF()

#         for indexer in range(len (FNO_LST_DF)):
#             script_name =FNO_LST_DF['Script'][indexer]
#             Script_code =FNO_LST_DF['Code'][indexer]
#             today = "2023-04-17 21:00"
#             df_frames =[]

#             for i in range (0,20):
#                 to_date = datetime.strptime(today, "%Y-%m-%d %H:%M")
#                 from_date = to_date+relativedelta(days=-100)
                
#                 to_date =to_date.strftime("%Y-%m-%d %H:%M")
#                 from_date =from_date.strftime("%Y-%m-%d %H:%M")

#                 today = from_date
#                 # print ("from date = {} to date ={}".format(from_date,to_date))
#                 Script_code=data.token_lookup(script_name)
#                 try:
#                     df_frames.append(data.get_eq_data(script_name, Script_code,from_date, to_date,"ONE_DAY").iloc[::-1].reset_index())
#                 except Exception as e:
#                     print(e)
#                     pass
#                 time.sleep(0.4)

#             rel_daily_df = pd.concat(df_frames,ignore_index=True)
#             file_name = f"candle_data/{script_name}_daily_df.csv"
#             rel_daily_df.to_csv(file_name, index=False)
#             print(script_name) 

#### Run following lines to collect lated DF data

# hist_data_obj =hist_data()
# hist_data_obj.store_daily_data()

# 05-Apr-2023
# FNO_LST = chandu_util.get_FNO_LST()
# for item in FNO_LST:
#     print(data.token_lookup(item))

# print(rel_5min_df)
# # data.get_iifl_data("TATA CONSULTANCY SERV LTD", "11536","2023-03-24 15:00", "2023-03-24 16:00","FIVE_MINUTE")
# print(data.token_lookup("AARTIIND"))
# rel_5min_df.to_csv('rel_5min_df.csv', index=False)
# rel_5min_df = pd.read_csv('rel_5min_df.csv')
# LSTM_model_obj.train_LSTM_model(rel_5min_df,76,"Rel","2885","5MIN","0920")

