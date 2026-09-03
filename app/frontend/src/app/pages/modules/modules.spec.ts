import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../../environments/environment';
import { ModulesComponent } from './modules';

describe('ModulesComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ModulesComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('renders a card per schema with its slots', async () => {
    const fixture = TestBed.createComponent(ModulesComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}asta/legality`).flush({
      schemi: [{ nome: '3-4-3', slots: [['Dc'], ['Dc', 'B']] }],
      roles: ['B', 'Dc'],
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('3-4-3');
    expect(text).toContain('Dc/B');
  });
});
