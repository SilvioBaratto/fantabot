export interface NewsRow {
  player_id: string;
  nome: string;
  sentiment: number;
  disponibilita: number;
  titolarita: number;
  forma: number;
  confidenza: number;
  ruoli_mantra: string;
}

export interface DriftRow {
  player_id: string;
  nome: string;
  ruoli_mantra: string;
  ruolo_campo: string;
  deriva_ruolo: number;
}
