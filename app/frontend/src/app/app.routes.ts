import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./shared/layout/layout').then((m) => m.LayoutComponent),
    children: [
      { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
      {
        path: 'dashboard',
        loadComponent: () => import('./pages/dashboard/dashboard').then((m) => m.DashboardComponent),
        title: 'Dashboard',
      },
      {
        path: 'asta',
        loadComponent: () => import('./pages/asta/asta').then((m) => m.AstaComponent),
        title: 'Asta',
      },
      {
        path: 'prices',
        loadComponent: () => import('./pages/prices/prices').then((m) => m.PricesComponent),
        title: 'Target prices',
      },
      {
        path: 'lineup',
        loadComponent: () => import('./pages/lineup/lineup').then((m) => m.LineupComponent),
        title: 'Lineup',
      },
      {
        path: 'modules',
        loadComponent: () => import('./pages/modules/modules').then((m) => m.ModulesComponent),
        title: 'Modules',
      },
      {
        path: 'accounts',
        loadComponent: () => import('./pages/accounts/accounts').then((m) => m.AccountsComponent),
        title: 'Accounts',
      },
      {
        path: 'synchronize',
        loadComponent: () =>
          import('./pages/synchronize/synchronize').then((m) => m.SynchronizeComponent),
        title: 'Synchronize',
      },
      {
        path: 'news',
        loadComponent: () => import('./pages/news/news').then((m) => m.NewsComponent),
        title: 'News',
      },
      {
        path: 'system',
        loadComponent: () => import('./pages/system/system').then((m) => m.SystemComponent),
        title: 'System',
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
