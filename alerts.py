import discord


def build_alert(
    title,
    description,
    alert_type="Crypto Monitor",
    url=None,
    fields=None
):
    embed = discord.Embed(
        title=title,
        description=description[:4000],
        color=discord.Color.blue()
    )

    if fields:
        for name, value, inline in fields:
            embed.add_field(
                name=name,
                value=str(value)[:1024],
                inline=inline
            )

    if url:
        embed.url = url

    embed.set_footer(
        text=f"Crypto Monitor • {alert_type}"
    )

    return embed


async def send_alert(
    channel,
    title,
    description,
    alert_type="Crypto Monitor",
    url=None,
    fields=None
):
    embed = build_alert(
        title=title,
        description=description,
        alert_type=alert_type,
        url=url,
        fields=fields
    )

    await channel.send(
        embed=embed
    )
