import discord


async def send_alert(
    channel,
    title,
    description,
    alert_type="Crypto Monitor",
    url=None
):
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blue()
    )

    embed.set_footer(
        text=f"Crypto Monitor • {alert_type}"
    )

    if url:
        embed.url = url

    await channel.send(embed=embed)
