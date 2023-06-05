import os
import sys
import logging
import datetime
from linebot import (LineBotApi, WebhookHandler)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,DatetimePickerTemplateAction,
    SourceUser, SourceGroup, SourceRoom,
    TemplateSendMessage, ConfirmTemplate, MessageAction,
    ButtonsTemplate, ImageCarouselTemplate, ImageCarouselColumn, URIAction,
    PostbackAction, DatetimePickerAction,
    CameraAction, CameraRollAction, LocationAction,
    CarouselTemplate, CarouselColumn, PostbackEvent,
    StickerMessage, StickerSendMessage, LocationMessage, LocationSendMessage,
    ImageMessage, VideoMessage, AudioMessage, FileMessage,
    UnfollowEvent, FollowEvent, JoinEvent, LeaveEvent, BeaconEvent,
    FlexSendMessage, BubbleContainer, ImageComponent, BoxComponent,
    TextComponent, IconComponent, ButtonComponent,
    SeparatorComponent, QuickReply, QuickReplyButton
)
from linebot.exceptions import (LineBotApiError, InvalidSignatureError)
import json
import boto3
from boto3.session import Session
from botocore.errorfactory import ClientError
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# バケット名を指定
BUCKET_NAME = "linebot-json"
object_key_name = "work_chedule.json"

session = boto3.session.Session()
s3_client = session.client("s3")    

logger = logging.getLogger()
logger.setLevel(logging.ERROR)

#LINEBOTと接続するための記述
#環境変数からLINEBotのチャンネルアクセストークンとシークレットを読み込む
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)

#部屋数単価330
room_unit_price = int(os.getenv('ROOM_UNIT_PRICE', None))
#朝番 500 帰番 1000 
shift_price_morning = int(os.getenv('SHIFT_PRICE_MORNING', None))
shift_price_late = int(os.getenv('SHIFT_PRICE_LATE', None))
#部屋チェック有無330
room_check_price = int(os.getenv('ROOM_CHECK_PRICE', None))
#新人指導1000
newcomer_guidance_price = int(os.getenv('NEWCOMER_GUIDANCE_PRICE', None))
#新人点検500
newcomer_check_price = int(os.getenv('NEWCOMER_CHECK_PRICE', None))

# サービスアカウントのJSONキー
JSON_FILE_NAME = os.getenv('JSON_FILE_NAME', None)
#スプレッドシートのキー
SPREAD_SHEET_ID = os.getenv('SPREAD_SHEET_KEY', None)

#無いならエラー
if channel_secret is None:
    logger.error('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    logger.error('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

#apiとhandlerの生成（チャンネルアクセストークンとシークレットを渡す）
line_bot_api = LineBotApi(channel_access_token)
handler = WebhookHandler(channel_secret)

#日給計算保持用変数の初期化
intWages = 0
intRoomNum = 0
intShiftPtn = 0
blnRoomCheck = False
blnNewComerGuidance = False
blnNewComerCheck = False

#Lambdaのメインの動作
def lambda_handler(event, context):

#認証用のx-line-signatureヘッダー
    signature = event["headers"]["x-line-signature"]
    body = event["body"]

#リターン値の設定
    ok_json = {"isBase64Encoded": False,
               "statusCode": 200,
               "headers": {},
               "body": ""}
    error_json = {"isBase64Encoded": False,
                  "statusCode": 500,
                  "headers": {},
                  "body": "Error"}

#メッセージを受け取る・受け取ったら受け取ったテキストを返信する
    @handler.add(MessageEvent, message=TextMessage)
    def message(line_event):
        global intRoomNum
        global intWages
        text = line_event.message.text

        if text.isnumeric() == True:
            intRoomNum = int(text)
            if intRoomNum >= 1 and intRoomNum <= 100:
                intWages = room_unit_price*intRoomNum
                messages = make_button_template(1)
                line_bot_api.reply_message(line_event.reply_token, messages)

        if text == '勤怠出力':
            
            scope = ['https://spreadsheets.google.com/feeds']
            # jsonファイル指定
            credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE_NAME, scope)
            gc = gspread.authorize(credentials)
            workbook = gc.open_by_key(SPREAD_SHEET_ID)
            sheet = workbook.get_worksheet(0)

            #ワークシートクリア
            sheet.clear()

            if check_s3_key_exists(object_key_name):

                # オブジェクトを生成
                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=object_key_name)
                # ファイルを読み込む
                body = response['Body'].read()
                item_dict = json.loads(body)
                wklist = item_dict["list"]

                # データの生成
                headers = ['date', 'intWages']
                column = []
                column.append(headers)

                for data in wklist:
                    row =[]
                    row.append(data['date'])
                    row.append(data['intWages'])
                    column.append(row)

                # スプレッドシートに挿入する
                workbook.values_append("data", {"valueInputOption": "USER_ENTERED"}, {"values": column})

                text="https://docs.google.com/spreadsheets/d/1VTgDXucVJgweqbLOcvifsOvo-Md8AkNQMjzx6wh4jAY/edit#gid=203832082" 
                line_bot_api.reply_message(line_event.reply_token, TextSendMessage(text=text))

        if text == 'no':
            text = make_result_message()
            reset()
            line_bot_api.reply_message(line_event.reply_token, TextSendMessage(text=text))            

#ボタンメッセージを受け取る・受け取ったら返信に応じたボタンテンプレートを返す
    @handler.add(PostbackEvent)
    def handle_postback(event):
        UserID = event.source.user_id
        global intWages
        global intShiftPtn
        global blnRoomCheck
        global blnNewComerGuidance
        global blnNewComerCheck

        podtback_msg = event.postback.data

        if event.postback.data == 'done':
            text = make_result_message()
            reset()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))

        if event.postback.data == 'calc_shift':
            messages = make_button_template(2)
            line_bot_api.reply_message(event.reply_token, messages)

        if event.postback.data == 'morning' or event.postback.data == 'late' or event.postback.data == 'both' or event.postback.data == 'skip1':
            if event.postback.data == 'morning':
                intShiftPtn = 1
                intWages = intWages + shift_price_morning
            elif event.postback.data == 'late':
                intShiftPtn = 2
                intWages = intWages + shift_price_late
            elif event.postback.data == 'both':
                intShiftPtn = 3
                intWages = intWages + shift_price_morning + shift_price_late
            messages = make_button_template(3)
            line_bot_api.reply_message(event.reply_token, messages)

        if event.postback.data == 'room_check_on' or event.postback.data == 'room_check_off' or event.postback.data == 'skip2':
            if event.postback.data == 'room_check_on':
                blnRoomCheck = True
                intWages = intWages + room_check_price
            messages = make_button_template(4)
            line_bot_api.reply_message(event.reply_token, messages)

        if event.postback.data == 'newcomer_guidance_on' or event.postback.data == 'newcomer_guidance_off' or event.postback.data == 'skip3':
            if event.postback.data == 'newcomer_guidance_on':
                blnNewComerGuidance = True
                intWages = intWages + newcomer_guidance_price
            messages = make_button_template(5)
            line_bot_api.reply_message(event.reply_token, messages)

        if event.postback.data == 'newcomer_check_on' or event.postback.data == 'newcomer_check_off' or event.postback.data == 'skip4':
            if event.postback.data == 'newcomer_check_on':
                blnNewComerCheck = True
                intWages = intWages + newcomer_check_price
            comfirm_template = TemplateSendMessage(
                alt_text="計算結果を保存",
                template=ConfirmTemplate(
                    text="計算結果を保存するチュン？",
                    actions=[
                        PostbackAction(
                            label='YES',
                            data='yes',
                        ),
                        MessageAction(
                            label='NO',
                            text='no')
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token,comfirm_template)

        if event.postback.data == 'yes':
            date_template = TemplateSendMessage(
                alt_text='日付選択',
                template=ButtonsTemplate(
                    text='日付を選択するチュン',
                    title='日付選択',
                    image_size='cover',
                    actions=[
                        DatetimePickerTemplateAction(
                        label='日付選択',
                        data='action=buy&itemid=1',
                        mode='date',
                        initial=str(datetime.date.today())
                        )
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token,date_template)
        elif event.postback.data == 'no':
            text = make_result_message()
            reset()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))

        if podtback_msg == "action=buy&itemid=1":

            if check_s3_key_exists(object_key_name):
                # オブジェクトを生成
                s3 = boto3.Session(region_name=None).resource('s3')
                obj = s3.Object(BUCKET_NAME,object_key_name)
                # ファイルを読み込む
                response = obj.get()    
                body = response['Body'].read()
                item_dict = json.loads(body)
                wklist = item_dict["list"]

                for data in wklist:
                    #キーが一致したら上書き
                    if data['date'] == event.postback.params['date']:
                        data['intWages'] = intWages
                        break
                #キーがなければ追加
                if not list(filter(lambda item : item['date'] == event.postback.params['date'], wklist)):
                    item_dict["list"].append({"date":event.postback.params['date'],"intWages":intWages})

                #変数をJSON変換し S3にPUTする
                obj.put(Body = json.dumps(item_dict, ensure_ascii=False))
            else:
                s3 = boto3.resource('s3')
                # オブジェクトを生成
                bucket = s3.Bucket(BUCKET_NAME)
                obj = bucket.Object(object_key_name)
                wklist ={"userid" : UserID, "list":[{"date":event.postback.params['date'],"intWages":intWages}]}
                obj.put(Body = json.dumps(wklist, ensure_ascii=False))

            #計算結果を出力
            text = make_result_message()
            reset()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))

#例外処理としての動作
    try:
        handler.handle(body, signature)
    except LineBotApiError as e:
        logger.error("Got exception from LINE Messaging API: %s\n" % e.message)
        for m in e.error.details:
            logger.error("  %s: %s" % (m.property, m.message))
        return error_json
    except InvalidSignatureError:
        return error_json

    return ok_json

def check_s3_key_exists(key):

    try:
        s3_client.head_object(
            Bucket = BUCKET_NAME,
            Key = object_key_name
        )       
        return True
    except ClientError:
        return False

def make_button_template(intNum):
    if intNum == 1:
        message_template = TemplateSendMessage(
                        alt_text='処理選択',
                        template=ButtonsTemplate(
                            title='処理選択',
                            text='計算を続ける場合は以下のメニューを選ぶチュン',
                            actions=[
                                PostbackAction(
                                    label='計算を続ける',
                                    display_text='計算を続ける',
                                    data='calc_shift'
                                ),
                                PostbackAction(
                                    label='終了',
                                    display_text='終了',
                                    data='done'
                                )
                            ]
                        )
                    )
    elif intNum ==2:
        message_template = TemplateSendMessage(
                        alt_text='シフト選択',
                        template=ButtonsTemplate(
                            title='シフト選択',
                            text='朝番・帰番を選ぶチュン',
                            actions=[
                                PostbackAction(
                                    label='朝番',
                                    display_text='朝番',
                                    data='morning'
                                ),
                                PostbackAction(
                                    label='帰番',
                                    display_text='帰番',
                                    data='late'
                                ),
                                PostbackAction(
                                    label='朝晩・帰番',
                                    display_text='帰番',
                                    data='both'
                                ),
                                PostbackAction(
                                    label='スキップ',
                                    display_text='スキップ',
                                    data='skip1'
                                )
                            ]
                        )
                    )
    elif intNum ==3:
        message_template = TemplateSendMessage(
                        alt_text='ルームチェック有無',
                        template=ButtonsTemplate(
                            title='ルームチェック有無',
                            text='ルームチェック有無を選ぶチュン',
                            actions=[
                                PostbackAction(
                                    label='ルームチェック有',
                                    display_text='ルームチェック有',
                                    data='room_check_on'
                                ),
                                PostbackAction(
                                    label='ルームチェックなし',
                                    display_text='ルームチェックなし',
                                    data='room_check_off'
                                ),
                                PostbackAction(
                                    label='スキップ',
                                    display_text='スキップ',
                                    data='skip2'
                                )
                            ]
                        )
                    )
    elif intNum ==4:
        message_template = TemplateSendMessage(
                        alt_text='新人教育有無',
                        template=ButtonsTemplate(
                            title='新人教育有無',
                            text='新人教育有無を選ぶチュン',
                            actions=[
                                PostbackAction(
                                    label='新人教育あり',
                                    display_text='新人教育あり',
                                    data='newcomer_guidance_on'
                                ),
                                PostbackAction(
                                    label='新人教育なし',
                                    display_text='新人教育なし',
                                    data='newcomer_guidance_off'
                                ),
                                PostbackAction(
                                    label='スキップ',
                                    display_text='スキップ',
                                    data='skip3'
                                )
                            ]
                        )
                    )
    elif intNum ==5:
        message_template = TemplateSendMessage(
                        alt_text='新人点検有無',
                        template=ButtonsTemplate(
                            title='新人点検有無',
                            text='新人点検有無を選ぶチュン',
                            actions=[
                                PostbackAction(
                                    label='新人点検あり',
                                    display_text='新人点検あり',
                                    data='newcomer_check_on'
                                ),
                                PostbackAction(
                                    label='新人点検なし',
                                    display_text='新人点検なし',
                                    data='newcomer_check_off'
                                ),
                                PostbackAction(
                                    label='スキップ',
                                    display_text='スキップ',
                                    data='skip4'
                                )
                            ]
                        )
                    )

    return message_template

def make_result_message():

    result_message = ""

    if intWages > 0:
        price = room_unit_price*intRoomNum
        result_message = str(room_unit_price) + " x " + str(intRoomNum) + " = " + str(price) + "円\n"
        if intShiftPtn == 1:
            result_message = result_message + "朝番手当: " + str(shift_price_morning) + "円\n"
        if intShiftPtn == 2:
            result_message = result_message + "帰り番手当: " + str(shift_price_late) + "円\n"
        if intShiftPtn == 3:
            price = shift_price_morning + shift_price_late
            result_message = result_message + "朝番・帰り番手当: " + str(price) + "円\n"
        if blnRoomCheck == True:
            result_message = result_message + "部屋チェックあり: " + str(room_check_price) + "円\n"
        if blnNewComerGuidance == True:
            result_message = result_message + "新人指導: " + str(newcomer_guidance_price) + "円\n"
        if blnNewComerCheck == True:
            result_message = result_message + "新人点検あり: " + str(newcomer_check_price) + "円\n"
        result_message = result_message + "日給合計: " + str(intWages) + "円\n"
        result_message = result_message + "計算を終了するチュン。\n再度計算を行う場合は、部屋数を数字で入力するチュン"

    return result_message

def reset():
    global intWages
    global intRoomNum
    global intShiftPtn
    global blnRoomCheck
    global blnNewComerGuidance
    global blnNewComerCheck

    intWages = 0
    intRoomNum = 0
    intShiftPtn = 0
    blnRoomCheck = False
    blnNewComerGuidance = False
    blnNewComerCheck = False

