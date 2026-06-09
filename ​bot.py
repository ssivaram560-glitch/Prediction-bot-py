
import asyncio
import aiohttp
import json
import os
import sys
import time
import random
from datetime import datetime
from telegram import Bot
from telegram.request import HTTPXRequest
from collections import Counter, deque

# ================= SAFE PRINT =================
def cprint(msg):
    print(msg)
    sys.stdout.flush()

# ================= CONFIG =================
BOT_TOKEN = "8735067591:AAHbQ1CJCJAwNS4PD9Z--XWOae6rsCdll5g"
CHAT_ID = "8321379592"
WIN_STICKER = "CAACAgUAAxkBAAEQ8P1p4jULRyLybS62u-O4wbl931ImgwAC7xMAAp8_2VZiQWf6f3O05DsE"
NUMBER_WIN_STICKER = "CAACAgUAAxkBAAEQwVBptnTvxpiq-ivF1Fr6Y3k8pfrH9AACERkAAqBZoVbtx3BiOZCU4ToE"

HISTORY_API = "https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json"
MAX_LIMIT = 30
TARGET_WINS = 5

# ================= TELEGRAM =================
request = HTTPXRequest(connection_pool_size=10)
bot = Bot(token=BOT_TOKEN, request=request)

# ================= GLOBAL STATE =================
last_sent_period = None
waiting_result = False
predicted_period = None
predicted_side = None
predicted_number = None
prediction_count = 0
total_wins = 0
total_losses = 0
predictionHistory = []
continuous_loss = 0
last_predicted_numbers = []
method_history = []
target_reached = False
double_down_active = False
number_repeat_count = {}
side_history = []

# ================= PERFORMANCE TRACKING =================
method_performance = {
    "Fibonacci": {"wins": 0, "losses": 0, "streak": 0},
    "Gap": {"wins": 0, "losses": 0, "streak": 0},
    "Advance": {"wins": 0, "losses": 0, "streak": 0},
    "Hunter": {"wins": 0, "losses": 0, "streak": 0},
    "EvenOdd": {"wins": 0, "losses": 0, "streak": 0},
    "HotCold": {"wins": 0, "losses": 0, "streak": 0}
}

hot_numbers = deque(maxlen=50)
cold_numbers = {}

# ================= UTILS =================
def getBigSmall(num):
    return "BIG" if num >= 5 else "SMALL"

def isEven(num):
    return num % 2 == 0

def get_last_5_digits(period):
    period_str = str(period)
    return period_str[-5:] if len(period_str) >= 5 else period_str

# ================= SAFE TELEGRAM =================
async def safe_send_message(text):
    try:
        await bot.send_message(CHAT_ID, text)
    except Exception as e:
        cprint(f"Telegram Error: {e}")

async def safe_send_sticker(sticker):
    try:
        await bot.send_sticker(CHAT_ID, sticker)
    except Exception as e:
        cprint(f"Sticker Error: {e}")

# ================= NEW PREDICTION LOGIC =================
def compute_best_num(history):
    n1 = int(history[0]["number"])
    n2 = int(history[1]["number"])

    # Core formula (your base logic)
    core = (n1 + n2 + 3) % 10

    # A few extra candidate formulas for variety
    candidates = [
        (n1 + n2 + 3) % 10,      # base
        (n1 * 2 + n2 + 3) % 10,  # weight latest more
        (n1 + n2 * 2 + 3) % 10,  # weight previous more
        (n1 + n2 + 5) % 10,      # shifted
    ]

    # Pick the side that the candidates agree on (majority vote)
    big_votes = sum(1 for c in candidates if getBigSmall(c) == "BIG")
    small_votes = len(candidates) - big_votes
    side = "BIG" if big_votes >= small_votes else "SMALL"

    # Confidence = how strong the agreement is
    agree = max(big_votes, small_votes)
    confidence = int((agree / len(candidates)) * 100)

    # Best number: prefer the core formula, but if it doesn't match the
    # voted side, pick the first candidate that does
    if getBigSmall(core) == side:
        bestNum = core
    else:
        bestNum = next((c for c in candidates if getBigSmall(c) == side), core)

    cprint(f"\n📊 LOGIC: n1={n1}, n2={n2} | candidates={candidates} "
           f"| {big_votes}B/{small_votes}S -> {side} {bestNum} ({confidence}%)")
    return side, bestNum, confidence

def finalDecision(history):
    side, bestNum, confidence = compute_best_num(history)
    return side, bestNum, confidence

def getSmartNumber(predict_side, period, history):
    _, bestNum, _ = compute_best_num(history)
    cprint(f"🎯 LOGIC NUMBER: {bestNum}")
    return bestNum, "NewLogic"

# ================= API =================
async def fetch_history(session):
    try:
        async with session.get(HISTORY_API) as r:
            if r.status != 200: return None
            return json.loads(await r.text())
    except: return None

# ================= RESULT CHECK - FIXED STICKER LOGIC =================
async def check_result(session):
    global waiting_result, continuous_loss, method_performance, total_wins, total_losses, target_reached, double_down_active
    if not waiting_result: return
    data = await fetch_history(session)
    if not data: return
    history = data["data"]["list"]
    latest = history[0]
    if str(latest["issueNumber"]) != predicted_period: return
    actual_num = int(latest["number"])
    actual_side = getBigSmall(actual_num)
    size_win = actual_side == predicted_side
    number_win = actual_num == predicted_number

    # Update stats
    is_win = size_win or number_win
    if is_win:
        total_wins += 1
        continuous_loss = 0
        double_down_active = False
    else:
        total_losses += 1
        continuous_loss += 1

    # Update method performance
    if method_history:
        last_method = method_history[-1]
        if last_method in method_performance:
            if is_win:
                method_performance[last_method]["wins"] += 1
                method_performance[last_method]["streak"] = max(0, method_performance[last_method]["streak"]) + 1
            else:
                method_performance[last_method]["losses"] += 1
                method_performance[last_method]["streak"] = min(0, method_performance[last_method]["streak"]) - 1

    predictionHistory.insert(0, {
        "period": predicted_period,
        "pred_side": predicted_side,
        "pred_number": predicted_number,
        "actual_number": actual_num,
        "actual_side": actual_side,
        "size_win": size_win,
        "number_win": number_win,
        "status": "WIN" if is_win else "LOSS"
    })

    # ================= STICKER LOGIC - CORRECT ORDER =================

    # STEP 1: Check SIZE WIN (First priority)
    if size_win:
        await safe_send_sticker(WIN_STICKER)
        await safe_send_message(f"✅ SIZE WIN!!! {predicted_side} Matched")
        cprint(f"✅ SIZE WIN: {predicted_side}")

    # STEP 2: Check NUMBER WIN (Second priority)
    if number_win:
        await safe_send_sticker(NUMBER_WIN_STICKER)
        await safe_send_message(f"🎯 NUMBER WIN!!! {predicted_side} {predicted_number} Matched")
        cprint(f"🎯 NUMBER WIN: {predicted_number}")

    # STEP 3: Check BOTH LOSS
    if not size_win and not number_win:
        loss_msg = f"❌ LOSS - Got {actual_side} {actual_num}"
        if double_down_active:
            cprint(f"⚠️ Double Down Active for next prediction")
        await safe_send_message(loss_msg)
        cprint(f"❌ LOSS: Predicted {predicted_side} {predicted_number}, Got {actual_side} {actual_num}")

    # Check target
    if is_win and total_wins >= TARGET_WINS:
        cprint(f"\n🎯 TARGET REACHED: {total_wins} WINS!")
        target_reached = True

    waiting_result = False

# ================= SESSION SUMMARY =================
async def print_session_summary():
    total_games = len(predictionHistory)
    wins = total_wins
    losses = total_losses
    size_wins = sum(1 for x in predictionHistory if x.get("size_win"))
    number_wins = sum(1 for x in predictionHistory if x.get("number_win"))
    max_streak = 0
    current_streak = 0
    for x in reversed(predictionHistory):
        if x["status"] == "WIN":
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else: current_streak = 0
    accuracy = (wins / total_games * 100) if total_games > 0 else 0
    cprint("\n🏁 SESSION COMPLETE")
    cprint("")
    cprint(f"🎮 Total Games: {total_games}")
    cprint(f"✅ Wins: {wins}")
    cprint(f"❌ Losses: {losses}")
    cprint(f"🔥 Max Win Streak: {max_streak}")
    cprint(f"🎯 Number Wins: {number_wins}")
    cprint(f"📊 Accuracy: {accuracy:.2f}%")
    if target_reached:
        cprint("\n🛑 Bot stopped after reaching target wins")
    cprint("")
    summary_msg = (
        "🏁 SESSION COMPLETE\n\n"
        f"🎮 Total Games: {total_games}\n"
        f"✅ Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"🔥 Max Win Streak: {max_streak}\n"
        f"🎯 Number Wins: {number_wins}\n"
        f"📊 Accuracy: {accuracy:.2f}%"
    )
    if target_reached:
        summary_msg += "\n\n🛑 Bot stopped after reaching target wins"
    await safe_send_message(summary_msg)

# ================= MAIN LOOP =================
async def main():
    global last_sent_period, waiting_result
    global predicted_period, predicted_side, predicted_number
    global prediction_count, target_reached
    cprint("🤖 MASS TAMIL VIP BOT - FINAL VERSION")
    cprint("===================================")
    cprint(f"🎯 Target Wins              : {TARGET_WINS}")
    cprint(f"Max Predictions per session : {MAX_LIMIT}")
    cprint(f"Telegram Chat ID           : {CHAT_ID}")
    cprint("\n🔬 ADVANCED SIZE PREDICTION LOGICS:")
    cprint("   1. 🔄 Pattern Breaker")
    cprint("   2. ⚖️ Weighted Confidence")
    cprint("   3. 📊 Moving Average Crossover")
    cprint("   4. 🎯 Support/Resistance Flip")
    cprint("   5. 🧮 Probability Engine")
    cprint("   6. ⚡ Momentum Shift Detector")
    cprint("\n✅ STICKER LOGIC:")
    cprint("   • Size Win → Win Sticker + Message")
    cprint("   • Number Win → Number Sticker + Message")
    cprint("   • Both Win → Both Stickers + Both Messages")
    cprint("   • Both Loss → Loss Message")
    cprint("===================================")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await safe_send_message(
            "🤖 OVERTHINKERBOT - FINAL\n"
            "🔬 6 Advanced Size Logics\n"
            "✅ Fixed Sticker Order\n"
            f"🎯 Target: {TARGET_WINS} Wins"
        )
        while prediction_count < MAX_LIMIT and not target_reached:
            await check_result(session)
            if target_reached: break
            data = await fetch_history(session)
            if not data:
                await asyncio.sleep(4)
                continue
            history = data["data"]["list"]
            next_period = str(int(history[0]["issueNumber"]) + 1)
            if next_period != last_sent_period and not waiting_result:
                side, bestNum, confidence = finalDecision(history)
                if not side:
                    last_sent_period = next_period
                    await asyncio.sleep(4)
                    continue
                num, method_used = getSmartNumber(side, next_period, history)
                predicted_period = next_period
                predicted_side = side
                predicted_number = num
                waiting_result = True
                last_sent_period = next_period
                prediction_count += 1
                period_display = get_last_5_digits(predicted_period)
                msg = f"🎁 OVERTHINKER PREDICTION 🎉\n\n"
                msg += f"🆔 Period ➜ {period_display}\n\n"
                msg += f"🛡 Predict ➜ {predicted_side} {num}\n"
                msg += f"📊 Confidence ➜ {confidence}%"
                await safe_send_message(msg)
                cprint(f"\n🎯 Predict: {predicted_side} {num} | Confidence: {confidence}%")
            await asyncio.sleep(4)
        await print_session_summary()
        if target_reached:
            cprint(f"\n✅ SUCCESS! Target of {TARGET_WINS} wins reached!")
            await safe_send_message(f"✅ SUCCESS! Target of {TARGET_WINS} wins reached!\n🤖 Bot shutting down...")
        else:
            cprint(f"\n⚠️ Max limit ({MAX_LIMIT}) reached without target")
    os._exit(0)

# ================= START =================
asyncio.run(main())
