import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import CalendarModule from '../CalendarModule';

const events = [
  { id: 'e1', title: 'Event 1', date: '2024-06-01', description: 'event details' },
];

describe('CalendarModule', () => {
  it('opens pop-up on event click', () => {
    render(<CalendarModule events={events} />);
    fireEvent.click(screen.getByText('Event 1'));
    expect(screen.getByTestId('event-popup')).toHaveTextContent('event details');
  });
});

