use anchor_lang::prelude::*;

declare_id!("Fg6PaFpoGXkYsidMpWxTWqkZsZR5Znhf4D6kF9yR3F5");

#[program]
pub mod fixture_program {
    use super::*;

    pub fn initialize(_context: Context<Initialize>) -> Result<()> {
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Initialize {}
