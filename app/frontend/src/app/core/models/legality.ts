export interface Schema {
  nome: string;
  slots: string[][];
}

export interface LegalityGrid {
  schemi: Schema[];
  roles: string[];
}
