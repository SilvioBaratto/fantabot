export interface PlanPlayer {
  player_id: string;
  nome: string;
  price: number;
}

export interface AstaPlan {
  found: boolean;
  listone: string;
  roster_size: number;
  total_cost: number;
  objective: number;
  budget: number;
  players: PlanPlayer[];
}
