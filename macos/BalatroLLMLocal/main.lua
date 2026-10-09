-- Additional public observations only. Never exports seed/draw order to the coach.
if os.getenv('BALATRO_LLM_LOCAL') ~= '1' then return end
assert(BB_GAMESTATE and BB_DISPATCHER, 'BalatroBot must load before local bridge')
local original = BB_GAMESTATE.get_gamestate
local suits = {Hearts='H', Diamonds='D', Clubs='C', Spades='S'}
local ranks = {['10']='T', Jack='J', Queen='Q', King='K', Ace='A'}
local unlocked = false

local function unlock_bot_profile()
  if unlocked or not G.PROFILES or not G.PROFILES[2] or G.SETTINGS.profile ~= 2 then return end
  G.PROFILES[2].all_unlocked = true
  for _, pool in ipairs({G.P_CENTERS, G.P_BLINDS, G.P_TAGS}) do
    for _, entry in pairs(pool or {}) do
      if not entry.demo and not entry.wip then
        entry.alerted = true; entry.discovered = true; entry.unlocked = true
      end
    end
  end
  unlocked = true
end

local function runtime(card)
  local a = card.ability or {}
  local e = type(a.extra) == 'table' and a.extra or {}
  local key = card.config and card.config.center and card.config.center.key
  local out = {mult=e.mult or a.mult, chips=e.chips or a.chips,
    x_mult=e.xmult or e.Xmult or a.x_mult or a.Xmult,
    h_size=e.h_size or (type(a.extra)=='number' and a.extra or nil), dollars=e.dollars}
  if key == 'j_ride_the_bus' then out.mult = a.mult or 0 end
  if key == 'j_red_card' then out.mult = a.mult or 0 end
  if key == 'j_trousers' then out.mult = e.mult or a.mult end
  if key == 'j_selzer' then out.extra = a.extra end
  if key == 'j_loyalty_card' then out.loyalty_remaining = a.loyalty_remaining end
  if key == 'j_drivers_license' then out.driver_tally = a.driver_tally end
  if key == 'j_todo_list' then out.poker_hand = e.poker_hand end
  if key == 'j_yorick' then out.remaining_discards = e.discards end
  if key == 'j_invisible' then out.invisible_rounds = a.invis_rounds end
  local cr = G.GAME.current_round or {}
  if key == 'j_idol' then
    out.idol_rank = cr.idol_card and (ranks[cr.idol_card.rank] or cr.idol_card.rank)
    out.idol_suit = cr.idol_card and suits[cr.idol_card.suit]
  end
  if key == 'j_mail' then out.mail_rank = cr.mail_card and (ranks[cr.mail_card.rank] or cr.mail_card.rank) end
  if key == 'j_castle' then out.castle_suit = cr.castle_card and suits[cr.castle_card.suit] end
  return out
end

function BB_GAMESTATE.get_gamestate()
  unlock_bot_profile()
  local s = original()
  s.local_bridge_version = 1
  s.bot_profile = G.SETTINGS.profile
  s.profile_mode = G.PROFILES and G.PROFILES[G.SETTINGS.profile]
    and G.PROFILES[G.SETTINGS.profile].all_unlocked and 'all_unlocked' or 'career'
  s.won = not not s.won
  s.pack_choices_remaining = G.GAME and G.GAME.pack_choices or 0
  s.deck_composition = {}
  local groups = {}
  for _, card in ipairs(G.playing_cards or {}) do
    local value = card.config and card.config.card or {}
    local a = card.ability or {}
    local enhancement = a.effect and a.effect ~= 'Base' and string.upper(a.effect:gsub(' Card','')) or nil
    local rank = ranks[value.value] or tostring(value.value or '?')
    local suit = suits[value.suit] or '?'
    if enhancement == 'STONE' then rank='?'; suit='?' end
    local edition = card.edition and card.edition.type and string.upper(card.edition.type)
    local seal = card.seal and string.upper(card.seal)
    local bonus = a.perma_bonus or 0
    local signature = table.concat({rank,suit,enhancement or '',edition or '',seal or '',tostring(bonus)}, '|')
    if not groups[signature] then
      groups[signature] = {rank=rank,suit=suit,enhancement=enhancement,edition=edition,
        seal=seal,permanent_bonus=bonus,count=0}
    end
    groups[signature].count = groups[signature].count + 1
  end
  local keys = {}; for key in pairs(groups) do keys[#keys+1] = key end; table.sort(keys)
  for _, key in ipairs(keys) do s.deck_composition[#s.deck_composition+1] = groups[key] end
  s.used_vouchers = {}
  for key in pairs(G.GAME.used_vouchers or {}) do s.used_vouchers[#s.used_vouchers+1] = key end
  table.sort(s.used_vouchers)
  s.round = s.round or {}
  s.round.boss_rerolled = not not (G.GAME.round_resets and G.GAME.round_resets.boss_rerolled)
  s.round.ancient_suit = G.GAME.current_round and G.GAME.current_round.ancient_card
    and suits[G.GAME.current_round.ancient_card.suit] or nil
  s.round.most_played_poker_hand = G.GAME.current_round and G.GAME.current_round.most_played_poker_hand
  s.last_tarot_planet = G.GAME.last_tarot_planet
  for _, blind in pairs(s.blinds or {}) do
    blind.disabled = blind.status == 'CURRENT' and G.GAME.blind and not not G.GAME.blind.disabled or false
    if blind.status == 'CURRENT' and G.GAME.blind then blind.score = G.GAME.blind.chips end
  end
  local areas = {hand=G.hand,cards=G.deck,jokers=G.jokers,consumables=G.consumeables,
    shop=G.shop_jokers,vouchers=G.shop_vouchers,packs=G.shop_booster,pack=G.pack_cards}
  for name, area in pairs(areas) do
    if s[name] and area then
      s[name].highlighted_limit = area.config.highlighted_limit or 5
      for i, raw in ipairs(s[name].cards or {}) do
        local card = area.cards[i]
        if card then
          raw.value.perma_bonus = card.ability and card.ability.perma_bonus or 0
          raw.state.forced_selection = not not (card.ability and card.ability.forced_selection)
          if raw.set == 'JOKER' and raw.state.hidden then
            s[name].cards[i] = {set='JOKER',state={hidden=true}}
          elseif raw.set == 'JOKER' then
            raw.value.ability = runtime(card)
            if card.ability.perishable then raw.modifier.perishable = card.ability.perish_tally or 0 end
          end
        end
      end
    end
  end
  return s
end

local health = BB_DISPATCHER.endpoints.health.execute
BB_DISPATCHER.endpoints.health.execute = function(args, send)
  unlock_bot_profile()
  health(args, function(s)
    s.profile_mode = G.PROFILES and G.PROFILES[2] and G.SETTINGS.profile == 2
      and G.PROFILES[2].all_unlocked and 'all_unlocked' or 'career'
    s.local_bridge_version = 1; s.bot_profile = G.SETTINGS.profile
    send(s)
  end)
end

if not BB_DISPATCHER.endpoints.reroll_boss then
  BB_DISPATCHER.register({name='reroll_boss',description='Use the normal Director Cut/Retcon boss reroll',
    schema={},requires_state={G.STATES.BLIND_SELECT},execute=function(_,send)
      local vouchers = G.GAME.used_vouchers or {}
      local allowed = vouchers.v_retcon or (vouchers.v_directors_cut and not G.GAME.round_resets.boss_rerolled)
      if not allowed or G.GAME.dollars - (G.GAME.bankrupt_at or 0) < 10 then
        send({name='BAD_REQUEST',message='Boss reroll requires an available voucher and $10'})
        return
      end
      G.FUNCS.reroll_boss({config={}})
      G.E_MANAGER:add_event(Event({trigger='after',delay=1,blocking=false,func=function()
        if G.CONTROLLER.locks.boss_reroll then return false end
        send(BB_GAMESTATE.get_gamestate()); return true
      end}))
    end})
end
