import discord
from discord import app_commands
import aiohttp
import asyncio
import os
from flask import Flask, request, jsonify
from threading import Thread

# === CẤU HÌNH BẮT BUỘC ===
TOKEN = "MTQ1MjI5Mzg4MzE5ODQ0MzY0Mw.G5HMCx.o1XOJmf90uG8GT_Bfdn3pJaw9n3TcYZ23RtW9E" 
ADMIN_ID = 1195292985416503347  # Đã điền ID Discord của bạn
LOG_CHANNEL_ID = 1518662178486489118 # Điền ID kênh nhận log đơn hàng thành công vào đây
BANK_NHAN_HANG = "ZaloPay" 
BANK_STK = "0968175643" 
BANK_TEN_CHU_TK = "TRAN DANG CUONG" # Viết hoa không dấu (Ví dụ: NGUYEN VAN A)

# Kho hàng UgPhone (Tên gói: [Giá tiền, [Danh sách mã/tài khoản bàn giao]])
STOCK = {
    "UVIP 10d 8h (Code)": [50000, []],
    "UVIP 36d (Code)": [175000, []],
    "GVIP 10d 8h (Code)": [55000, []],
    "GVIP 36d (Code)": [185000, []],
    "MVIP 10d 8h (Code)": [140000, []],
    "MVIP 30d (Code)": [365000, []],
    "SVIP 10d 8h (Code)": [150000, []],
    "SVIP 30d (Code)": [500000, []],
    "GVIP 15d (Share)": [60000, []],
    "GVIP 30d (Share)": [125000, []],
    "SVIP 7d (Share)": [85000, []],
    "SVIP 15d (Share)": [145000, []],
    "SVIP 30d (Share)": [255000, []]
}

# Lưu trữ các đơn hàng đang chờ thanh toán tạm thời
PENDING_ORDERS = {} 

# === KHỞI TẠO BOT ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# === GIAO DIỆN PANEL MUA HÀNG ===
class PurchaseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
        options = []
        for name, info in STOCK.items():
            options.append(discord.SelectOption(
                label=name, 
                description=f"Giá: {info[0]:,} VNĐ - Còn lại: {len(info[1])}", 
                value=name
            ))
            
        self.select = discord.ui.Select(placeholder="Chọn gói UgPhone bạn muốn mua...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        product_name = self.select.values[0]
        price = STOCK[product_name][0]
        stock_count = len(STOCK[product_name][1])

        if stock_count == 0:
            await interaction.response.send_message("❌ Sản phẩm này hiện tại đã hết hàng! Vui lòng đợi Admin thêm hàng hoặc liên hệ đặt trước.", ephemeral=True)
            return

        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        ticket_channel = await guild.create_text_channel(
            name=f"🛒-{interaction.user.name}", 
            overwrites=overwrites,
            category=interaction.channel.category
        )

        order_id = f"DH{interaction.user.id}{int(asyncio.get_event_loop().time()) % 1000}"
        
        PENDING_ORDERS[order_id] = {
            "user_id": interaction.user.id,
            "product": product_name,
            "price": price,
            "channel_id": ticket_channel.id
        }

        # Tạo mã VietQR hỗ trợ quét thẳng vào ZaloPay
        qr_url = f"https://img.vietqr.io/image/970403-{BANK_STK}-compact2.png?amount={price}&addInfo={order_id}&accountName={BANK_TEN_CHU_TK.replace(' ', '%20')}"

        embed = discord.Embed(title="💳 THÔNG TIN THANH TOÁN AUTOMATIC ZALOPAY", color=discord.Color.green())
        embed.add_field(name="Sản phẩm UgPhone", value=product_name, inline=False)
        embed.add_field(name="Số tiền cần chuyển", value=f"{price:,} VNĐ", inline=True)
        embed.add_field(name="Nội dung CK bắt buộc", value=f"**{order_id}**", inline=True)
        embed.set_image(url=qr_url)
        embed.set_footer(text="Hệ thống tự động check bill sau 10-15s khi nhận được tiền.")

        view = discord.ui.View()
        cancel_btn = discord.ui.Button(label="Hủy Đơn Hàng", style=discord.ButtonStyle.danger)
        
        async def cancel_callback(inter):
            if inter.user.id != interaction.user.id:
                return await inter.response.send_message("Không phải đơn của bạn!", ephemeral=True)
            if order_id in PENDING_ORDERS:
                del PENDING_ORDERS[order_id]
            await inter.response.send_message("Đang hủy đơn và xóa phòng ticket...")
            await asyncio.sleep(3)
            await ticket_channel.delete()

        cancel_btn.callback = cancel_callback
        view.add_item(cancel_btn)

        await ticket_channel.send(content=f"{interaction.user.mention} Đơn hàng của bạn đã được tạo thành công!", embed=embed, view=view)
        await interaction.response.send_message(f"✅ Đã tạo ticket thanh toán tại: {ticket_channel.mention}", ephemeral=True)

# === LỆNH ADMIN (SLASH COMMANDS) ===
@bot.tree.command(name="setup_shop", description="Gửi bảng menu mua bán hàng vào kênh hiện tại (Chỉ Admin)")
async def setup_shop(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này!", ephemeral=True)
    
    embed = discord.Embed(
        title="🏪 HỆ THỐNG BÁN HÀNG UGPHONE TỰ ĐỘNG 24/7",
        description="Chào mừng bạn đến với shop! Hãy chọn sản phẩm ở menu bên dưới để tiến hành mua hàng tự động qua Ví ZaloPay.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message("Đang thiết lập cửa hàng...", ephemeral=True)
    await interaction.channel.send(embed=embed, view=PurchaseView())

@bot.tree.command(name="add_stock", description="Thêm hàng vào kho (Chỉ Admin)")
async def add_stock(interaction: discord.Interaction, sản_phẩm: str, nội_dung_hàng: str):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Không có quyền!", ephemeral=True)
    
    if sản_phẩm in STOCK:
        STOCK[sản_phẩm][1].append(nội_dung_hàng)
        await interaction.response.send_message(f"✅ Đã thêm 1 mã hàng vào gói **{sản_phẩm}**. Hiện có trong kho: {len(STOCK[sản_phẩm][1])}")
    else:
        await interaction.response.send_message("❌ Tên sản phẩm không đúng! Phải nhập chính xác tên gói.")

# === XỬ LÝ TRẢ HÀNG KHI THÀNH CÔNG ===
async def process_success_order(order_id):
    if order_id not in PENDING_ORDERS:
        return False
        
    order = PENDING_ORDERS[order_id]
    product = order["product"]
    user_id = order["user_id"]
    channel_id = order["channel_id"]

    guild = bot.guilds[0]
    member = await guild.fetch_member(user_id)
    channel = bot.get_channel(channel_id)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)

    if len(STOCK[product][1]) > 0:
        item_data = STOCK[product][1].pop(0)
    else:
        item_data = "LỖI: Hết hàng trong kho lúc giao dịch, liên hệ Admin giải quyết!"

    delivery_embed = discord.Embed(title="🎉 GIAO HÀNG THÀNH CÔNG", color=discord.Color.gold())
    delivery_embed.add_field(name="Sản phẩm UgPhone", value=product, inline=False)
    delivery_embed.add_field(name="Mã Code / Thông tin nhận được", value=f"```\n{item_data}\n```", inline=False)
    
    try:
        await member.send(embed=delivery_embed)
        delivery_status = "Đã gửi qua tin nhắn riêng (DM)."
    except:
        if channel:
            await channel.send(content=f"{member.mention} Do bạn chặn DM nên bot giao hàng tại đây luôn nhé:", embed=delivery_embed)
        delivery_status = "Gửi trực tiếp tại Ticket vì khách chặn DM."

    if channel:
        await channel.send("✅ **Thanh toán thành công!** Hàng đã được giao qua tài khoản của bạn. Kênh này sẽ tự động xóa sau 30 giây.")
        asyncio.create_task(delete_channel_later(channel, 30))

    if log_channel:
        log_embed = discord.Embed(title="💰 ĐƠN HÀNG UGPHONE THÀNH CÔNG", color=discord.Color.green())
        log_embed.add_field(name="Khách hàng", value=member.name, inline=True)
        log_embed.add_field(name="Sản phẩm", value=product, inline=True)
        log_embed.add_field(name="Số tiền", value=f"{order['price']:,} VNĐ", inline=True)
        log_embed.add_field(name="Mã đơn", value=order_id, inline=True)
        log_embed.add_field(name="Trạng thái giao", value=delivery_status, inline=False)
        await log_channel.send(embed=log_embed)

    del PENDING_ORDERS[order_id]
    return True

async def delete_channel_later(channel, delay):
    await asyncio.sleep(delay)
    try:
        await channel.delete()
    except:
        pass

# === WEBHOOK SERVER ===
app = Flask('')

@app.route('/sepay-webhook', methods=['POST'])
def sepay_webhook():
    data = request.json
    if data:
        content = data.get("content", "")
        amount = data.get("transferAmount", 0)

        for order_id, order_info in PENDING_ORDERS.items():
            if order_id.lower() in content.lower() and int(amount) >= int(order_info["price"]):
                asyncio.run_coroutine_threadsafe(process_success_order(order_id), bot.loop)
                return jsonify({"status": "success", "message": "Đơn hàng hợp lệ, đã xử lý trả hàng"}), 200

    return jsonify({"status": "ignored", "message": "Không khớp đơn hàng nào"}), 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

@bot.event
async def on_ready():
    print(f'🤖 Bot đã online với tên: {bot.user}')
    t = Thread(target=run_flask)
    t.start()

bot.run(TOKEN)
  
